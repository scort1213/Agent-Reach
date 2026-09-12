"""Public Xiaoyuzhou metadata and Get transcription; no private API or local ASR."""

import contextlib
import hashlib
import io
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import requests
from bs4 import BeautifulSoup

from . import get_client
from .weread_helper.file_lock import exclusive_lock

URL = re.compile(r'https://www\.xiaoyuzhoufm\.com/(episode|podcast)/([a-f0-9]{24})/?\Z')


def fetch(url):
    if not URL.fullmatch(url):
        raise ValueError('需要小宇宙公开单集或节目链接')
    with requests.get(url, timeout=30, allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            raise ValueError(f'小宇宙 HTTP {response.status_code}，未跳转或换接口')
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 8 * 1024 * 1024:
                raise ValueError('节目页面过大')
            chunks.append(chunk)
    node = BeautifulSoup(b''.join(chunks), 'html.parser').find('script', id='__NEXT_DATA__')
    if node is None:
        raise ValueError('公开页面没有可识别数据；可能需登录或验证')
    return json.loads(node.get_text())['props']['pageProps']


def episode(data):
    eid = data.get('eid', '')
    if not re.fullmatch('[a-f0-9]{24}', eid):
        raise ValueError('单集编号缺失')
    source = (data.get('media') or {}).get('source') or {}
    media = source.get('url') or ''
    allowed = (source.get('mode') == 'PUBLIC' and not data.get('isPrivateMedia')
               and data.get('payType', 'FREE') == 'FREE'
               and urlparse(media).scheme == 'https'
               and urlparse(media).hostname == 'media.xyzcdn.net')
    show = data.get('podcast') or {}
    return {'episode_id': eid, 'url': f'https://www.xiaoyuzhoufm.com/episode/{eid}',
            'title': data.get('title'), 'podcast_id': data.get('pid'),
            'podcast': show.get('title'), 'author': show.get('author'),
            'published_at': data.get('pubDate'), 'duration_s': data.get('duration'),
            'shownotes': BeautifulSoup(data.get('shownotes') or data.get('description') or '', 'html.parser').get_text('\n', strip=True),
            'media_url': media if allowed else None, 'status': 'discovered' if allowed else 'access_unavailable'}


def discover(url, limit):
    if limit < 1:
        raise ValueError('数量须为正整数')
    data = fetch(url)
    if '/episode/' in url:
        items = [episode(data['episode'])]
        if items[0]['url'].rstrip('/') != url.rstrip('/'):
            raise ValueError('返回单集与输入链接不符')
        return items, True, 1
    show = data['podcast']
    if show.get('pid') != url.rstrip('/').split('/')[-1]:
        raise ValueError('节目编号不符')
    unique = {e['eid']: e for e in show.get('episodes', [])}
    items = [episode({**e, 'podcast': e.get('podcast') or show}) for e in unique.values()]
    total = show.get('episodeCount')
    return items[:limit], isinstance(total, int) and len(items) == total, total


def collect(source, output, limit=20, no_submit=False, max_minutes=15,
            prepare_audio=False, audio_note_id=None, audio_note_title=None):
    if limit < 1 or not math.isfinite(max_minutes) or max_minutes <= 0:
        raise ValueError('数量和额度须为有效正数')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(output / 'podcast.lock'):
        existing = output / 'job.json'
        if existing.exists() and json.loads(existing.read_text(encoding="utf-8")).get('identity') != [source, limit]:
            raise ValueError('输出目录属于另一任务')
        try:
            if audio_note_title:
                data, _ = get_client.Client().request('/resource/note/list?cursor=0')
                matches = [n for n in data.get('notes', [])
                           if n.get('title') == audio_note_title and n.get('note_type') == 'local_audio']
                if len(matches) != 1:
                    raise ValueError('最新20条中未唯一定位音频笔记；请核对标题或提供数字note_id')
                audio_note_id = str(matches[0]['note_id'])
            return _collect(source, output, limit, no_submit, max_minutes, prepare_audio, audio_note_id)
        except (ValueError, RuntimeError, requests.RequestException, OSError) as error:
            path = output / 'job.json'
            state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {'platform': 'xiaoyuzhou'}
            state.setdefault('failures', []).append({'type': type(error).__name__})
            state.update(status='blocked', error={'type': type(error).__name__,
                         'message': str(error) if isinstance(error, (ValueError, RuntimeError))
                         else '网络或文件操作失败；保留任务，不自动重提'})
            get_client.save(path if path.exists() else output / 'last-error.json', state)
            return state


def _collect(source, output, limit, no_submit, max_minutes, prepare_audio, audio_note_id):
    if max_minutes <= 0:
        raise ValueError('转写分钟数须为正数')
    path = output / 'job.json'
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get('identity') != [source, limit]:
            raise ValueError('输出目录属于另一任务')
    else:
        items, complete, total = discover(source, limit)
        state = {'platform': 'xiaoyuzhou', 'identity': [source, limit], 'items': items,
                 'discovery_complete': complete, 'reported_total': total,
                 'selection_fulfilled': '/episode/' in source or len(items) == limit,
                 'reserved_seconds': 0, 'budget_seconds': max_minutes * 60,
                 'status': 'collecting'}
        get_client.save(path, state)
    state.setdefault('selection_fulfilled', '/episode/' in source or len(state['items']) == limit)
    if max_minutes * 60 != state['budget_seconds']:
        raise ValueError('续跑须保留原任务额度')
    if (prepare_audio or audio_note_id) and len(state['items']) != 1:
        raise ValueError('音频导入只接受单集任务')
    for item in state['items']:
        if item['status'] in {'complete', 'access_unavailable'}:
            continue
        if no_submit and not prepare_audio and not audio_note_id:
            continue
        duration = item.get('duration_s')
        if not isinstance(duration, (int, float)) or duration <= 0:
            item.update(status='duration_unknown')
            continue
        record_path = output / (item['episode_id'] + '.json')
        if audio_note_id:
            if not re.fullmatch(r'[0-9]{1,30}', audio_note_id):
                raise ValueError('需要Get API返回的数字note_id，网页地址中的短编号不能直接使用')
            if not item.get('audio_file') or not item.get('audio_reserved'):
                raise ValueError('先准备并在Get网页导入本单集音频，再关联笔记')
            note, _ = get_client.Client().request('/resource/note/detail?' + urlencode({'id': audio_note_id}))
            original = (note.get('note', {}).get('audio') or {}).get('original')
            if not isinstance(original, str) or not original.strip():
                raise ValueError('该笔记没有音频原文，不能验收')
            target = output / (item['episode_id'] + '-audio.original.txt')
            target.write_text(original, encoding="utf-8", newline="\n")
            item.update(status='awaiting_analysis', original_file=str(target),
                        original_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                        note_id=audio_note_id, identity_verification='agent_required')
        elif prepare_audio:
            if not item.get('audio_reserved'):
                if state['reserved_seconds'] + duration > state['budget_seconds']:
                    item.update(status='budget_exceeded')
                    continue
                state['reserved_seconds'] += duration
                item['audio_reserved'] = True
                get_client.save(path, state)
            extension = Path(urlparse(item['media_url']).path).suffix.lower()
            if extension not in {'.m4a', '.mp3', '.wav', '.aac', '.mp4'}:
                raise ValueError('无法确认音频文件格式')
            target = output / (item['episode_id'] + extension)
            if not item.get('audio_file'):
                with requests.get(item['media_url'], timeout=30, allow_redirects=False, stream=True) as response:
                    if response.status_code != 200:
                        raise ValueError(f'音频 HTTP {response.status_code}，停止')
                    temp = target.with_suffix('.part')
                    size = 0
                    try:
                        with temp.open('xb') as handle:
                            for chunk in response.iter_content(65536):
                                size += len(chunk)
                                if size > 128 * 1024 * 1024:
                                    raise ValueError('音频超过128MB')
                                handle.write(chunk)
                        temp.replace(target)
                    finally:
                        temp.unlink(missing_ok=True)
                item['audio_file'] = str(target)
            item.update(status='needs_get_audio_import')
        else:
            if not item.get('link_reserved'):
                if state['reserved_seconds'] + duration > state['budget_seconds']:
                    item.update(status='budget_exceeded')
                    continue
                state['reserved_seconds'] += duration
                item['link_reserved'] = True
                get_client.save(path, state)
            with contextlib.redirect_stdout(io.StringIO()):
                get_client.main([str(output), item['episode_id'], item['url']])
            record = json.loads(record_path.read_text(encoding="utf-8"))
            item['get_record'] = str(record_path)
            item['note_id'] = record.get('note_id')
            fields = record.get('fields', {})
            # A returned page may be only shownotes. Agent must establish full audio coverage.
            field = fields.get('audio.original') or fields.get('web_page.content')
            if field and not record.get('quality_warning'):
                item.update(status='awaiting_analysis', original_file=field['file'],
                            original_sha256=field['sha256'], identity_verification='agent_required')
            else:
                item.update(status='needs_review', reason=record.get('status'))
        get_client.save(path, state)
    state.pop('error', None)
    statuses = {i['status'] for i in state['items']}
    state['status'] = ('discovered' if no_submit and not prepare_audio and not audio_note_id and statuses == {'discovered'} else
                       'complete' if statuses == {'complete'} and state.get('selection_fulfilled') else
                       'awaiting_analysis' if statuses <= {'complete', 'awaiting_analysis'} and statuses
                       else 'partial')
    get_client.save(path, state)
    return state


def review(output, data):
    path = output / 'job.json'
    state = json.loads(path.read_text(encoding="utf-8"))
    item = next(i for i in state['items'] if i['episode_id'] == data['episode_id'])
    if item['status'] != 'awaiting_analysis':
        raise ValueError('单集尚无待验收原文')
    original = Path(item['original_file'])
    if hashlib.sha256(original.read_bytes()).hexdigest() != item['original_sha256']:
        raise ValueError('原文已变化，重新核对')
    text = original.read_text(encoding="utf-8")
    if data.get('identity_verified') is not True or data.get('full_audio_verified') is not True:
        raise ValueError('Agent须确认单集身份及完整音频内容，不以节目说明验收')
    required = ['quote', 'conclusion', 'value', 'structure', 'doubts']
    if any(not isinstance(data.get(k), str) or not data[k].strip() for k in required):
        raise ValueError('缺少分析与证据')
    if data['quote'] not in text or len(data['quote']) < 8:
        raise ValueError('引文不在原文中')
    target = output / (item['episode_id'] + '-analysis.md')
    target.write_text('# ' + item['title'] + '\n\n来源：' + item['url'] + '\n\n' +
                      '\n\n'.join(f'## {k}\n\n{data[k]}' for k in required), encoding="utf-8", newline="\n")
    item.update(status='complete', report=str(target),
                review_method=data.get('review_method', 'agent_content_review'))
    state['status'] = 'complete' if state.get('selection_fulfilled') and all(i['status'] == 'complete' for i in state['items']) else 'partial'
    get_client.save(path, state)
    return state
