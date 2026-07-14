'use strict';

/**
 * Maps yt-dlp stderr output to a stable error code and an operator-friendly
 * message. Order matters: the first matching pattern wins.
 */
const PATTERNS = [
  { re: /private video/i, code: 'PRIVATE', message: 'Video is private' },
  { re: /video unavailable|has been removed|no longer available/i, code: 'REMOVED', message: 'Video removed or unavailable' },
  { re: /not available in your country|geo.?(restricted|blocked)/i, code: 'REGION_BLOCKED', message: 'Region blocked' },
  { re: /age.?restricted|sign in to confirm your age/i, code: 'AGE_RESTRICTED', message: 'Age restricted (sign-in required)' },
  { re: /copyright/i, code: 'COPYRIGHT', message: 'Blocked for copyright reasons' },
  { re: /live event has ended|live stream recording is not available/i, code: 'LIVE_ENDED', message: 'Live stream has ended' },
  { re: /premieres? in|is not yet available/i, code: 'NOT_STARTED', message: 'Stream has not started yet' },
  { re: /members.?only|join this channel/i, code: 'MEMBERS_ONLY', message: 'Members-only content' },
  { re: /unable to download|network|timed? ?out|getaddrinfo|ECONNRE/i, code: 'NETWORK', message: 'Network failure while contacting YouTube' },
  { re: /unsupported url|is not a valid url|invalid url/i, code: 'INVALID_URL', message: 'Not a valid YouTube URL' },
];

function classifyYtdlpError(stderr) {
  const text = String(stderr || '');
  for (const p of PATTERNS) {
    if (p.re.test(text)) return { code: p.code, message: p.message };
  }
  const line = text.split('\n').find((l) => l.startsWith('ERROR:'));
  return {
    code: 'RESOLVE_FAILED',
    message: line ? line.replace(/^ERROR:\s*/, '').slice(0, 200) : 'Failed to resolve video',
  };
}

module.exports = { classifyYtdlpError };
