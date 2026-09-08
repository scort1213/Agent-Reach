// Agent Reach listing-only adapter for wechat-article-search. See NOTICE.md.
const fs = require('node:fs');
const cheerio = require('cheerio');
const {parseArticlesFromSearchHtml} = require('./parser.cjs');

function parseListing(html, limit) {
  const $ = cheerio.load(html);
  const title = $('title').text();
  if (/验证码|访问过于频繁|用户您好，您的访问|安全验证/.test(title)
      || $('#seccodeImage, #seccodeInput, form[action*="antispider"]').length
      || $('iframe[src*="captcha"], script[src*="captcha"]').length) {
    throw new Error('blocked: 搜狗要求验证；本次搜索未完成，不能视为零结果。');
  }
  const empty = $('.no-result, .no-results, .results-not-found').text();
  if (/没有找到|未找到|暂无.*结果|没有相关/.test(empty)) return [];
  if (!$('ul.news-list > li h3 a').length) {
    throw new Error('unexpected_page: 未识别到搜索结果结构，不能视为零结果。');
  }
  const articles = parseArticlesFromSearchHtml(html, limit);
  if (!articles.length) throw new Error('parse_error: 搜索卡片存在，但解析失败。');
  for (const row of articles) {
    if (!row.title || !row.url) throw new Error('parse_error: 文章缺少标题或链接。');
    const url = new URL(row.url);
    if (url.protocol !== 'https:' || url.hostname !== 'weixin.sogou.com'
        || url.pathname !== '/link') {
      throw new Error('unexpected_link: 结果链接不是预期的搜狗文章链接。');
    }
    for (const key of Object.keys(row)) {
      if (row[key] === '') row[key] = null;
    }
  }
  return articles;
}

if (require.main === module) {
  try {
    if (process.argv[2] === '--probe') {
      // A real local parse verifies the runtime without contacting a website.
      const result = parseListing('<ul class="news-list"><li><h3><a href="/link?url=probe">probe</a></h3></li></ul>', 1);
      if (result[0].title !== 'probe') throw new Error('parser probe failed');
      console.log('parser-ready');
    } else {
      const limit = Number(process.argv[2]);
      if (!Number.isInteger(limit) || limit < 1 || limit > 10) throw new Error('limit must be 1–10');
      console.log(JSON.stringify(parseListing(fs.readFileSync(0, 'utf8'), limit)));
    }
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
module.exports = {parseListing};
