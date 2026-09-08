"""Shared browser font policy for the Admin and Archive sites."""

PRETENDARD_VERSION = "1.3.9"
WEB_FONT_ORIGIN = "https://cdn.jsdelivr.net"
WEB_FONT_URL = (
    f"{WEB_FONT_ORIGIN}/npm/pretendard@{PRETENDARD_VERSION}/"
    "dist/web/variable/woff2/PretendardVariable.woff2"
)
WEB_FONT_CSP_SOURCE = WEB_FONT_URL

WEB_FONT_FACE_CSS = f"""@font-face {{
  font-family: 'Pretendard Variable';
  font-style: normal;
  font-weight: 45 920;
  font-display: swap;
  src: local('Pretendard Variable'), url('{WEB_FONT_URL}') format('woff2-variations');
}}"""

# Apple SD Gothic Neo is licensed as an Apple system font, not a redistributable
# webfont. Apple devices use their local copy; other platforms download the
# open-licensed Pretendard fallback.
WEB_FONT_STACK = (
    "'Apple SD Gothic Neo', 'AppleSDGothicNeo-Regular', "
    "'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, "
    "'Segoe UI', 'Malgun Gothic', sans-serif"
)
