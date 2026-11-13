/*
 * What a street address looks like, for both harnesses.
 *
 * The public area (Thạnh Mỹ Tây, TP.HCM) may appear in automated mail and in
 * Standing needs!B2. The school's street address may not, so the harnesses
 * match its shape rather than one literal: a house number (optionally "số" or "No.",
 * optionally a "12/3" alley form) followed by a street word, in Vietnamese or
 * English.
 */
'use strict';

const NUMBER = '\\d+[a-z]?(?:\\/\\d+[a-z]?)*';
const VI_STREET = '(?:đường|duong|phố|pho|hẻm|hem|ngõ|ngo|kiệt|kiet)';
const EN_STREET = '(?:street|st\\.|road|rd\\.|avenue|ave\\.|lane|alley|boulevard|blvd\\.?)';

const ADDRESS = new RegExp(
  '(?:số|no\\.)\\s*' + NUMBER + '(?![\\d/])' +
  '|' + NUMBER + ',?\\s+' + VI_STREET + '(?![a-zà-ỹđ])' +
  '|' + NUMBER + ',?\\s+(?:[^\\s,.\\d]+\\s+){0,3}' + EN_STREET + '(?![a-z])',
  'iu');

const MUST_MATCH = [
  '123 Đường Lê Lợi',
  'số 45 đường Nguyễn Trãi',
  'Số 7/3 Phan Xích Long',
  '12/4 Hẻm 30',
  '12 Nguyen Trai Street',
  '88 Le Loi St.',
  '5, Road 3'
];
const MUST_NOT_MATCH = [
  'Khu vực của chúng tôi: Thạnh Mỹ Tây, TP.HCM.',
  'Our area: Thạnh Mỹ Tây, Ho Chi Minh City.',
  'Bạn cũng có thể nhắn Zalo hoặc WhatsApp: <số>.',
  'at least 3 months before the expiry date',
  'more than roughly 20 kg or 20 L',
  'Tue or Thu works.'
];

module.exports = { ADDRESS, MUST_MATCH, MUST_NOT_MATCH };
