import test from 'node:test';
import assert from 'node:assert/strict';
import { attachmentName, calendarLabel } from '../../src/reframe/web/js/names.js';

test('file attachments show the file name only', () => {
  assert.equal(attachmentName('C:\\Projects\\PRJ-20\\Spares\\Order 1234 rev B.xlsx', 'file'), 'Order 1234 rev B.xlsx');
  assert.equal(attachmentName('\\\\server\\share\\drawings\\A-100.pdf', 'file'), 'A-100.pdf');
  assert.equal(attachmentName('/home/me/notes.txt', 'file'), 'notes.txt');
  assert.equal(attachmentName('C:\\Projects\\Folder\\', 'file'), 'Folder');
  assert.equal(attachmentName('C:\\', 'file'), 'C:');
});

test('URL attachments show the last path segment, else the host', () => {
  assert.equal(attachmentName('https://example.com/docs/spec%20v2.pdf?x=1', 'url'), 'spec v2.pdf');
  assert.equal(attachmentName('https://example.com/', 'url'), 'example.com');
  assert.equal(attachmentName('mailto:a@b.com?subject=hi', 'url'), 'a@b.com');
  assert.equal(attachmentName('not a url', 'url'), 'not a url');
});

test('calendar label shows name and folder path, and survives unknown ids', () => {
  const cals = { A: { name: 'United States holidays', path: '\\\\me@x.com\\Calendar\\United States holidays' },
    B: { name: 'Calendar', path: '0000000007A5A625' }, C: { name: 'Calendar', path: '' } };
  assert.equal(calendarLabel(cals, 'A'), 'United States holidays (\\\\me@x.com\\Calendar\\United States holidays)');
  assert.equal(calendarLabel(cals, 'B'), 'Calendar');          // an opaque id is not a path worth showing
  assert.equal(calendarLabel(cals, 'C'), 'Calendar');
  assert.match(calendarLabel(cals, 'Z'), /Unknown calendar/);
  assert.equal(calendarLabel(cals, null), '');
});
