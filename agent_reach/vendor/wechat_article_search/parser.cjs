// Adapted from zjp1997720/wechat-article-search (MIT). See NOTICE.md and LICENSE.
const cheerio = require("cheerio");

function parseArticlesFromSearchHtml(html, maxResults) {
  const articles = [];
  const $ = cheerio.load(html);

  const $newsList = $('ul.news-list');
  if ($newsList.length === 0) return [];

  $newsList.find('li').each((_, element) => {
    if (articles.length >= maxResults) return false;
    const article = parseArticle($, element);
    if (article) {
      articles.push(article);
    }
  });

  return articles;
}

function parseRelativeTime(timeText) {
  if (!timeText) return { datetime: '', dateText: '' };

  const now = new Date();
  let targetDate = new Date(now);

  // 匹配各种相对时间格式
  const dayMatch = timeText.match(/(\d+)天前/);
  const hourMatch = timeText.match(/(\d+)小时前/);
  const minuteMatch = timeText.match(/(\d+)分钟前/);

  if (dayMatch) {
    const days = parseInt(dayMatch[1]);
    targetDate.setDate(now.getDate() - days);
  } else if (hourMatch) {
    const hours = parseInt(hourMatch[1]);
    targetDate.setHours(now.getHours() - hours);
  } else if (minuteMatch) {
    const minutes = parseInt(minuteMatch[1]);
    targetDate.setMinutes(now.getMinutes() - minutes);
  } else {
    // 尝试匹配标准日期格式（如"2024-01-15"）
    const dateMatch = timeText.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (dateMatch) {
      targetDate = new Date(`${dateMatch[1]}-${dateMatch[2]}-${dateMatch[3]}T00:00:00+08:00`);
    } else {
      return { datetime: '', dateText: timeText };
    }
  }

  const datetime = formatChinaDateTime(targetDate);
  const dateText = datetime.slice(0, 10).replace('-', '年').replace('-', '月') + '日';

  return { datetime, dateText };
}

/**
 * 将Date对象格式化为中国时区（UTC+8）的datetime字符串
 * @param {Date} date - Date对象
 * @returns {string} YYYY-MM-DD HH:mm:ss 格式的中国时间
 */
function formatChinaDateTime(date) {
  // 转换为中国时间（UTC+8）
  const chinaTime = new Date(date.getTime() + 8 * 60 * 60 * 1000);
  const year = chinaTime.getUTCFullYear();
  const month = String(chinaTime.getUTCMonth() + 1).padStart(2, '0');
  const day = String(chinaTime.getUTCDate()).padStart(2, '0');
  const hours = String(chinaTime.getUTCHours()).padStart(2, '0');
  const minutes = String(chinaTime.getUTCMinutes()).padStart(2, '0');
  const seconds = String(chinaTime.getUTCSeconds()).padStart(2, '0');
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

/**
 * 解析单篇文章
 * @param {Object} $ - cheerio实例
 * @param {Object} element - 文章DOM元素
 * @returns {Object|null} 文章数据对象
 */
function parseArticle($, element) {
  try {
    const $elem = $(element);

    // 获取标题和URL
    const $titleLink = $elem.find('h3 a');
    if ($titleLink.length === 0) return null;

    const title = $titleLink.text().trim();
    let url = $titleLink.attr('href') || '';

    // 处理相对URL
    if (url.startsWith('/')) {
      url = `https://weixin.sogou.com${url}`;
    }

    // 获取概要
    const summary = $elem.find('p.txt-info').text().trim();

    // 获取日期和来源
    let datetime = '';
    let dateText = '';
    let source = '';
    let timeDescription = ''; // 原始时间文字描述（如"2小时前"）

    const $sourceBox = $elem.find('.s-p');
    if ($sourceBox.length > 0) {
      // 获取日期 - 优先从script标签获取时间戳
      const $dateScript = $sourceBox.find('.s2 script');
      if ($dateScript.length > 0) {
        const scriptText = $dateScript.text();
        const timestampMatch = scriptText.match(/(\d{10})/);
        if (timestampMatch) {
          const timestamp = parseInt(timestampMatch[1]) * 1000;
          const date = new Date(timestamp);
          datetime = formatChinaDateTime(date);
          dateText = datetime.slice(0, 10).replace('-', '年').replace('-', '月') + '日';
        }
      }

      // 尝试从文本获取时间描述（优先保存原始描述）
      const $timeElem = $sourceBox.find('.s2');
      if ($timeElem.length > 0) {
        // 获取script中的时间戳用于计算
        const scriptText = $timeElem.find('script').text();
        const timestampMatch = scriptText.match(/(\d{10})/);

        if (timestampMatch) {
          // 如果有时间戳，计算相对时间描述
          const timestamp = parseInt(timestampMatch[1]) * 1000;
          const articleDate = new Date(timestamp);
          const now = new Date();
          const diffMs = now - articleDate;
          const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
          const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

          if (diffDays > 0) {
            timeDescription = `${diffDays}天前`;
          } else if (diffHours > 0) {
            timeDescription = `${diffHours}小时前`;
          } else {
            const diffMinutes = Math.floor(diffMs / (1000 * 60));
            if (diffMinutes > 0) {
              timeDescription = `${diffMinutes}分钟前`;
            } else {
              timeDescription = '刚刚';
            }
          }
        } else {
          // 如果没有时间戳，尝试从文本获取
          const timeText = $timeElem.clone().children('script').remove().end().text().trim();
          if (timeText && !datetime) {
            timeDescription = timeText;
            const parsedTime = parseRelativeTime(timeText);
            datetime = parsedTime.datetime;
            dateText = parsedTime.dateText;
          }
        }
      }

      // 获取来源公众号名称 - 从 .all-time-y2 或 a.account 获取
      const $sourceSpan = $sourceBox.find('.all-time-y2');
      const $sourceLink = $sourceBox.find('a.account');
      if ($sourceSpan.length > 0) {
        source = $sourceSpan.text().trim();
      } else if ($sourceLink.length > 0) {
        source = $sourceLink.text().trim();
      }
    }

    return {
      title,
      url,
      summary,
      datetime,
      date_text: dateText,
      date_description: timeDescription || dateText,
      source
    };
  } catch (error) {
    console.error('解析文章失败:', error.message);
    return null;
  }
}


module.exports = {parseArticlesFromSearchHtml};
