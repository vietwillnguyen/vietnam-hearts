/*
 * What the automated mail and Standing needs!B2 must never contain, for both
 * harnesses: a street address, opening hours, or a promise to reply by a date.
 *
 * The public area (Thạnh Mỹ Tây, TP.HCM) may appear, and so may ordinary
 * quantities like "20 kg" or "3 months before expiry", so each matcher looks
 * for a shape rather than one literal, in Vietnamese or English.
 */
'use strict';

const NUMBER = '\\d+[a-z]?(?:\\/\\d+[a-z]?)*';
const VI_STREET = '(?:đường|duong|phố|pho|hẻm|hem|ngõ|ngo|kiệt|kiet)';
const EN_STREET = '(?:street|st\\.|road|rd\\.|avenue|ave\\.|lane|alley|boulevard|blvd\\.?)';

/* A house number (optionally "số" or "No.", optionally a "12/3" alley form)
   followed by a street word. */
const ADDRESS = new RegExp(
  '(?:số|no\\.)\\s*' + NUMBER + '(?![\\d/])' +
  '|' + NUMBER + ',?\\s+' + VI_STREET + '(?![a-zà-ỹđ])' +
  '|' + NUMBER + ',?\\s+(?:[^\\s,.\\d]+\\s+){0,3}' + EN_STREET + '(?![a-z])',
  'iu');

/* A clock time ("8h", "8h30", "8 giờ", "8:00", "8am") or the words for
   opening hours. */
const HOURS = new RegExp(
  '(?<![\\w#&])\\d{1,2}\\s*(?:h|giờ|gio)(?:\\s*\\d{2})?(?![a-zà-ỹđ])' +
  '|(?<![\\w#&:])\\d{1,2}:\\d{2}(?!\\d)' +
  '|(?<![\\w#&])\\d{1,2}\\s*(?:am|pm|a\\.m\\.|p\\.m\\.)(?![a-z])' +
  '|mở cửa|mo cua|opening hours|open(?:ing)? (?:from|at|daily)|office hours',
  'iu');

/* A reply-by promise: "within 3 days", "in 2 days", "trong 7 ngày",
   "trong vòng 48 giờ". */
const UNIT = '(?:days?|business days?|hours?|weeks?|ngày|giờ|tiếng|tuần)';
const DATE_PROMISE = new RegExp(
  '(?:within|in)\\s+(?:\\d+|one|two|three|four|five|six|seven|a few)\\s+' + UNIT +
  '|trong(?:\\s+vòng)?\\s+\\d+\\s*' + UNIT,
  'iu');

const SAMPLES = {
  ADDRESS: {
    match: [
      '123 Đường Lê Lợi',
      'số 45 đường Nguyễn Trãi',
      'Số 7/3 Phan Xích Long',
      '12/4 Hẻm 30',
      '12 Nguyen Trai Street',
      '88 Le Loi St.',
      '5, Road 3'
    ],
    pass: [
      'Khu vực của chúng tôi: Thạnh Mỹ Tây, TP.HCM.',
      'Our area: Thạnh Mỹ Tây, Ho Chi Minh City.',
      'Bạn cũng có thể nhắn Zalo hoặc WhatsApp: <số>.',
      'at least 3 months before the expiry date',
      'more than roughly 20 kg or 20 L',
      'Tue or Thu works.'
    ]
  },
  HOURS: {
    match: [
      'Mở cửa 8h - 17h',
      'from 8h30',
      'lúc 8 giờ sáng',
      '8:00-17:00',
      'open 9am to 5pm',
      'Giờ mở cửa: thứ Ba',
      'Opening hours: Tuesday'
    ],
    pass: [
      'Thạnh Mỹ Tây, Ho Chi Minh City.',
      'more than roughly 20 kg or 20 L',
      'at least 2 weeks before expiry',
      '<p style="color:#6b4f3a">',
      'Một tình nguyện viên sẽ nhắn bạn để hẹn giờ và địa điểm'
    ]
  },
  DATE_PROMISE: {
    match: [
      'We will reply within 3 days',
      'within seven days',
      'in 2 days',
      'trong 7 ngày',
      'trong vòng 48 giờ'
    ],
    pass: [
      'at least 3 months before the expiry date',
      'Milk cartons: at least 2 weeks before expiry.',
      'Sữa hộp: còn hạn ít nhất 2 tuần.',
      'Tue or Thu works.'
    ]
  }
};

module.exports = { ADDRESS, HOURS, DATE_PROMISE, SAMPLES };
