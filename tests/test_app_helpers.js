// Run with: node tests/test_app_helpers.js
const assert = require('assert');
const app = require('../web/app.js');

assert.strictEqual(app.upperBound([1, 3, 3, 7], 3), 3);
assert.strictEqual(app.upperBound([1, 3, 3, 7], 0), 0);
assert.strictEqual(app.upperBound([1, 3, 3, 7], 9), 4);

// A stale update keeps the last reading; a gap that is not stale (not answered yet) stays empty.
assert.deepStrictEqual(app.forwardFill([0.5, null, null, -0.2], [0, 1, 0, 0]), [0.5, 0.5, null, -0.2]);
assert.deepStrictEqual(app.ema([1, 0, null, 1], 0.5), [1, 0.5, null, 1]);

assert.strictEqual(app.signed(0.456), '+0.46');
assert.strictEqual(app.signed(-0.7, 1), '−0.7');
assert.strictEqual(app.signed(null), '–');

// Two half-innings: the break between them is cut from the video.
const anchors = [{video: 754, wall: 1000}, {video: 1580, wall: 2100}];
assert.strictEqual(app.videoToWall(anchors, 800), 1046);
assert.strictEqual(app.videoToWall(anchors, 1600), 2120);
assert.strictEqual(app.wallToVideo(anchors, 1046), 800);
assert.strictEqual(app.wallToVideo(anchors, 2120), 1600);
assert.strictEqual(app.wallToVideo(anchors, 2000), 1580); // during the cut break: the next stretch's start

// 2025-10-25T02:32:30Z is 10:32:30 PM Eastern on the 24th.
assert.strictEqual(app.etStamp(1761359550), '2025-10-24 22:32:30');
assert.strictEqual(app.etClock(1761359550), '10:32:30 PM ET');
assert.strictEqual(app.halfLabel(6, 'bottom'), 'Bot 6th');
assert.strictEqual(app.streamLabel('r/baseball:LAD-flair'), 'r/baseball · Dodgers flair');
assert.strictEqual(app.streamLabel('r/Dodgers'), 'r/Dodgers');

const comments = {s: [0, 0, 1, 0, 1]};
const tags = {subject: [0, 0, 0, 1, -1], sentiment: [-1, -0.5, 1, null, 0.3]};
const subjects = ['Addison Barger', 'other'];
assert.deepStrictEqual(app.subjectsInRange(comments, tags, subjects, 0, 5, -1), [{name: 'Addison Barger', count: 3, sentiment: -0.5 / 3}]);
assert.deepStrictEqual(app.subjectsInRange(comments, tags, subjects, 0, 5, 0), [{name: 'Addison Barger', count: 2, sentiment: -0.75}]);

const palette = {pos: '#0000ff', neg: '#ff0000', mid: '#808080'};
assert.strictEqual(app.sentimentColor(1, palette), 'rgb(0, 0, 255)');
assert.strictEqual(app.sentimentColor(-1, palette), 'rgb(255, 0, 0)');
assert.strictEqual(app.sentimentColor(null, palette), '#808080');

console.log('app helpers OK');
