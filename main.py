
import os
import re
import json
import html
import logging
import calendar
import smtplib
import feedparser

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google import genai
from google.genai import types


# ============================================================
# 1. 기본 설정
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
)

EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")

KST = ZoneInfo("Asia/Seoul")

MAX_AGE_HOURS = 72
MAX_ARTICLES = 40
ARTICLES_PER_FEED = 8

HISTORY_FILE = "processed_articles.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# 2. 뉴스 수집 대상
# ============================================================

def google_news_rss(query, language="ko"):
    """
    Google News RSS 검색 주소 생성
    최근 3일 이내 기사 검색
    """

    encoded_query = quote_plus(query + " when:3d")

    if language == "en":
        return (
            "https://news.google.com/rss/search?"
            f"q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
        )

    return (
        "https://news.google.com/rss/search?"
        f"q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
    )


RSS_FEEDS = [
    {
        "name": "벤처스퀘어 뉴스",
        "url": "https://www.venturesquare.net/rss/news"
    },
    {
        "name": "벤처스퀘어 인터뷰",
        "url": "https://www.venturesquare.net/rss/interview"
    },
    {
        "name": "벤처스퀘어 트렌드",
        "url": "https://www.venturesquare.net/rss/trend"
    },

    # 국내 주요 투자사
    {
        "name": "매쉬업벤처스",
        "url": google_news_rss("매쉬업벤처스 투자")
    },
    {
        "name": "소풍벤처스",
        "url": google_news_rss("소풍벤처스 투자")
    },
    {
        "name": "MYSC",
        "url": google_news_rss("MYSC 투자")
    },
    {
        "name": "블루포인트파트너스",
        "url": google_news_rss("블루포인트파트너스 투자")
    },
    {
        "name": "알토스벤처스",
        "url": google_news_rss("알토스벤처스 투자")
    },

    # 글로벌 투자사
    {
        "name": "Y Combinator",
        "url": google_news_rss(
            '"Y Combinator" startup funding',
            language="en"
        )
    },

    # 관심 산업
    {
        "name": "기후·환경",
        "url": google_news_rss(
            "기후테크 환경테크 스타트업 투자"
        )
    },
    {
        "name": "핀테크",
        "url": google_news_rss(
            "핀테크 스타트업 투자"
        )
    },
    {
        "name": "임팩트 투자",
        "url": google_news_rss(
            "임팩트 투자 소셜벤처"
        )
    },
    {
        "name": "인프라",
        "url": google_news_rss(
            "인프라 스타트업 투자"
        )
    },
    {
        "name": "콘텐츠",
        "url": google_news_rss(
            "콘텐츠 스타트업 투자"
        )
    },
    {
        "name": "국내 스타트업 투자",
        "url": google_news_rss(
            "스타트업 투자 유치 시리즈A 시리즈B"
        )
    },
    {
        "name": "글로벌 임팩트 투자",
        "url": google_news_rss(
            "impact investing startup funding",
            language="en"
        )
    }
]


# ============================================================
# 3. 기사 처리 이력
# ============================================================

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return set()

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        return set(data.get("processed_urls", []))

    except Exception as error:
        logger.warning("이력 파일을 읽지 못했습니다: %s", error)
        return set()


def save_history(processed_urls):
    data = {
        "processed_urls": list(processed_urls)[-500:]
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# 4. RSS 뉴스 수집
# ============================================================

def clean_html(text):
    if not text:
        return ""

    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def fetch_latest_news(processed_urls):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)

    articles = []
    seen_urls = set()
    seen_titles = set()

    for feed_info in RSS_FEEDS:
        feed_name = feed_info["name"]
        feed_url = feed_info["url"]

        try:
            feed = feedparser.parse(feed_url)

            if feed.bozo:
                logger.warning(
                    "RSS 파싱 경고 (%s): %s",
                    feed_name,
                    feed.bozo_exception
                )

            entries = feed.entries[:ARTICLES_PER_FEED]

            for entry in entries:
                url = entry.get("link", "").strip()
                title = clean_html(entry.get("title", ""))

                if not url or not title:
                    continue

                if url in processed_urls or url in seen_urls:
                    continue

                normalized_title = re.sub(
                    r"\s+", "", title.lower()
                )

                if normalized_title in seen_titles:
                    continue

                published = entry.get("published_parsed")

                if not published:
                    published = entry.get("updated_parsed")

                if not published:
                    continue

                published_timestamp = calendar.timegm(published)

                published_utc = datetime.fromtimestamp(
                    published_timestamp,
                    tz=timezone.utc
                )

                if published_utc < cutoff:
                    continue

                summary = clean_html(
                    entry.get("summary", "")
                )

                if not summary:
                    summary = clean_html(
                        entry.get("description", "")
                    )

                article = {
                    "id": f"A{len(articles) + 1:03d}",
                    "source": feed_name,
                    "title": title,
                    "url": url,
                    "published": published_utc.astimezone(
                        KST
                    ).strftime("%Y-%m-%d %H:%M"),
                    "summary": summary[:700]
                }

                articles.append(article)

                seen_urls.add(url)
                seen_titles.add(normalized_title)

                if len(articles) >= MAX_ARTICLES:
                    break

            logger.info(
                "%s: %d개 기사 확인",
                feed_name,
                len(entries)
            )

        except Exception:
            logger.exception(
                "RSS 수집 실패: %s",
                feed_name
            )

        if len(articles) >= MAX_ARTICLES:
            break

    logger.info("최종 수집 기사 수: %d", len(articles))

    return articles


# ============================================================
# 5. Gemini 분석
# ============================================================

def analyze_news_with_gemini(articles):
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")

    client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    articles_json = json.dumps(
        articles,
        ensure_ascii=False,
        indent=2
    )

    prompt = f"""
당신은 벤처캐피털(VC), 액셀러레이터(AC),
엔젤투자자의 관점에서 스타트업과 임팩트 투자를 분석하는
전문 리서치 애널리스트입니다.

아래 기사 데이터를 분석하고 한국어 투자 브리핑을 작성하세요.

[분석 목적]

사용자는 향후 VC, AC, 엔젤투자조합 등에서
투자 관련 업무를 수행할 수 있는 역량을 쌓고자 합니다.

관심 산업:
- 환경 및 기후테크
- 핀테크
- 임팩트 투자
- 인프라
- 콘텐츠

관심 투자사:
- 매쉬업벤처스
- 소풍벤처스
- MYSC
- Y Combinator
- 블루포인트파트너스
- 알토스벤처스

[중요한 분석 원칙]

1. 기사에 명시된 사실과 AI의 추론을 반드시 구분하세요.
2. 기사에 없는 매출, 고객 수, 투자 금액, 기업 실적을
   절대로 만들어내지 마세요.
3. 확인할 수 없는 내용은 "확인되지 않음"이라고 표시하세요.
4. 투자 유치와 실제 투자 집행을 혼동하지 마세요.
5. 단순 기사 요약보다 투자자가 주목해야 할 이유를 설명하세요.
6. 투자사의 투자 배경은 가설로 제시하고 근거를 설명하세요.
7. 리스크는 사업성, 시장성, 경쟁, 규제, 수익성,
   임팩트 측정 측면에서 검토하세요.
8. 각 분석에는 근거가 된 기사 ID를 반드시 포함하세요.
9. 기사에 없는 내용을 사실처럼 단정하지 마세요.
10. 관련성이 낮은 기사는 억지로 투자 이벤트로 분류하지 마세요.

[요청하는 분석]

1. 오늘의 핵심 요약
   - 가장 중요한 시장 변화
   - 주목할 투자 이벤트
   - 투자자가 기억해야 할 핵심 메시지

2. 투자 이벤트
   - 투자 유치, 후속 투자, M&A, 신규 사업 등
   - 투자 단계 및 투자 금액은 기사에 나온 경우에만 기재
   - 투자 배경에 대한 가설
   - 사업 모델과 성장 가능성
   - 임팩트 관점의 의미
   - 투자 리스크
   - 추가로 확인해야 할 정보
   - 관련 기사 ID

3. 시장 신호
   - 여러 기사에서 반복적으로 드러나는 변화
   - 산업의 성장 가능성 또는 위기
   - 투자 자금의 흐름과 투자자 관심 변화

4. LinkedIn 포스팅 아이디어
   - 사용자가 직접 글을 작성할 수 있도록
     제목, 핵심 관점, 생각해 볼 질문 제시
   - 완성된 게시글을 대신 작성하지 마세요.

5. 투자 학습 노트
   - 오늘 기사에서 배울 수 있는 투자 개념
   - 초보 투자자가 이해해야 할 용어
   - 실제 투자 검토에 적용할 질문

[출력 형식]

반드시 아래 JSON 구조를 따르세요.
JSON 이외의 설명이나 Markdown 코드 펜스를 출력하지 마세요.

{{
  "daily_summary": "오늘의 핵심 요약",
  "investment_events": [
    {{
      "title": "이벤트 제목",
      "category": "투자유치/후속투자/M&A/신사업/기타",
      "source_ids": ["A001"],
      "confirmed_facts": ["기사로 확인된 사실"],
      "investment_hypotheses": ["투자 배경에 대한 가설"],
      "impact_analysis": ["임팩트 관점의 분석"],
      "risks": ["투자 리스크"],
      "unknowns": ["추가 확인이 필요한 정보"],
      "relevance_score": 1
    }}
  ],
  "market_signals": [
    {{
      "signal": "시장 신호",
      "evidence": "근거",
      "investment_implication": "투자 관점의 의미",
      "source_ids": ["A001"]
    }}
  ],
  "linkedin_ideas": [
    {{
      "headline": "포스팅 제목",
      "angle": "핵심 관점",
      "question": "독자에게 던질 질문",
      "source_ids": ["A001"]
    }}
  ],
  "learning_notes": [
    {{
      "concept": "투자 개념",
      "explanation": "개념 설명",
      "application": "실제 투자 검토에서의 활용"
    }}
  ]
}}

relevance_score는 1~5 사이의 정수입니다.
관련성이 낮은 이벤트는 제외하세요.

[분석 대상 기사]

{articles_json}
"""

    logger.info(
        "Gemini 분석 시작: 모델 %s",
        GEMINI_MODEL
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
            max_output_tokens=7000,
            thinking_config=types.ThinkingConfig(
                thinking_level="low"
            )
        )
    )

    response_text = response.text

    if not response_text:
        raise ValueError("Gemini가 비어 있는 응답을 반환했습니다.")

    # JSON 파싱
    response_text = response_text.strip()

    response_text = re.sub(
        r"^```(?:json)?\s*",
        "",
        response_text
    )

    response_text = re.sub(
        r"\s*```$",
        "",
        response_text
    )

    try:
        result = json.loads(response_text)

    except json.JSONDecodeError:
        start = response_text.find("{")
        end = response_text.rfind("}")

        if start == -1 or end == -1:
            raise ValueError(
                "Gemini 응답에서 JSON을 찾을 수 없습니다."
            )

        result = json.loads(
            response_text[start:end + 1]
        )

    if not isinstance(result, dict):
        raise ValueError("Gemini 응답 형식이 올바르지 않습니다.")

    # 누락된 필드 기본값 처리
    result.setdefault("daily_summary", "")
    result.setdefault("investment_events", [])
    result.setdefault("market_signals", [])
    result.setdefault("linkedin_ideas", [])
    result.setdefault("learning_notes", [])

    return result


# ============================================================
# 6. 이메일 HTML 생성
# ============================================================

def safe_text(value):
    if isinstance(value, list):
        return "<br>".join(
            html.escape(str(item))
            for item in value
        )

    return html.escape(str(value or ""))


def source_links(source_ids, article_map):
    links = []

    for source_id in source_ids or []:
        article = article_map.get(source_id)

        if article:
            title = html.escape(article["title"])
            url = html.escape(article["url"], quote=True)

            links.append(
                f'<a href="{url}">{title}</a>'
            )

    if not links:
        return "연결된 출처 없음"

    return " | ".join(links)


def build_email_html(analysis, articles):
    article_map = {
        article["id"]: article
        for article in articles
    }

    today = datetime.now(KST).strftime("%Y년 %m월 %d일")

    sections = []

    sections.append(f"""
    <h1>Daily Investment Briefing</h1>
    <p style="color:#666;">{today} | Impact Investment Research</p>
    <hr>
    """)

    # 오늘의 요약
    sections.append(f"""
    <h2>1. 오늘의 핵심 요약</h2>
    <div style="background:#f5f5f5;padding:16px;border-radius:8px;">
        {safe_text(analysis.get("daily_summary"))}
    </div>
    """)

    # 투자 이벤트
    sections.append("<h2>2. 주요 투자 이벤트</h2>")

    events = analysis.get("investment_events", [])

    if not events:
        sections.append("<p>주목할 만한 투자 이벤트가 없습니다.</p>")

    for index, event in enumerate(events, 1):
        sections.append(f"""
        <h3>2-{index}. {safe_text(event.get("title"))}</h3>
        <p><b>분류:</b> {safe_text(event.get("category"))}</p>
        <p><b>관련성:</b> {safe_text(event.get("relevance_score"))}/5</p>

        <p><b>확인된 사실</b><br>
        {safe_text(event.get("confirmed_facts"))}</p>

        <p><b>투자 배경에 대한 가설</b><br>
        {safe_text(event.get("investment_hypotheses"))}</p>

        <p><b>임팩트 분석</b><br>
        {safe_text(event.get("impact_analysis"))}</p>

        <p><b>투자 리스크</b><br>
        {safe_text(event.get("risks"))}</p>

        <p><b>추가 확인 사항</b><br>
        {safe_text(event.get("unknowns"))}</p>

        <p><b>출처:</b>
        {source_links(event.get("source_ids", []), article_map)}
        </p>
        <hr>
        """)

    # 시장 신호
    sections.append("<h2>3. 시장 신호</h2>")

    for signal in analysis.get("market_signals", []):
        sections.append(f"""
        <h3>{safe_text(signal.get("signal"))}</h3>
        <p><b>근거:</b><br>
        {safe_text(signal.get("evidence"))}</p>

        <p><b>투자 관점:</b><br>
        {safe_text(signal.get("investment_implication"))}</p>

        <p><b>출처:</b>
        {source_links(signal.get("source_ids", []), article_map)}
        </p>
        """)

    # LinkedIn 아이디어
    sections.append("<h2>4. LinkedIn 포스팅 아이디어</h2>")

    for idea in analysis.get("linkedin_ideas", []):
        sections.append(f"""
        <h3>{safe_text(idea.get("headline"))}</h3>
        <p><b>핵심 관점:</b><br>
        {safe_text(idea.get("angle"))}</p>

        <p><b>생각해 볼 질문:</b><br>
        {safe_text(idea.get("question"))}</p>

        <p><b>출처:</b>
        {source_links(idea.get("source_ids", []), article_map)}
        </p>
        <hr>
        """)

    # 학습 노트
    sections.append("<h2>5. 투자 학습 노트</h2>")

    for note in analysis.get("learning_notes", []):
        sections.append(f"""
        <h3>{safe_text(note.get("concept"))}</h3>
        <p><b>개념:</b><br>
        {safe_text(note.get("explanation"))}</p>

        <p><b>투자 검토에 적용:</b><br>
        {safe_text(note.get("application"))}</p>
        """)

    # 원문 기사 목록
    sections.append("<h2>6. 수집한 원문 기사</h2>")

    for article in articles:
        url = html.escape(article["url"], quote=True)

        sections.append(f"""
        <p>
        <b>[{safe_text(article["id"])}]</b>
        <a href="{url}">{safe_text(article["title"])}</a><br>
        <small>
        출처: {safe_text(article["source"])}
        | 발행: {safe_text(article["published"])}
        </small><br>
        {safe_text(article["summary"])}
        </p>
        """)

    sections.append("""
    <hr>
    <p style="color:#888;font-size:12px;">
    본 리포트는 공개 뉴스와 AI 분석을 바탕으로 작성되었습니다.
    투자 판단 전에는 원문 및 기업의 공식 자료를 확인해야 합니다.
    AI가 제시한 투자 배경과 시장 전망은 가설이며,
    검증된 사실과 구분해서 활용하세요.
    </p>
    """)

    return f"""
    <html>
    <body style="
        font-family:Arial,sans-serif;
        line-height:1.65;
        color:#222;
        max-width:800px;
        margin:auto;
        padding:20px;
    ">
    {''.join(sections)}
    </body>
    </html>
    """


# ============================================================
# 7. 이메일 발송
# ============================================================

def send_email(subject, html_content):
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        raise ValueError(
            "EMAIL_USER 또는 EMAIL_APP_PASSWORD가 설정되지 않았습니다."
        )

    plain_text = re.sub(
        r"<[^>]+>",
        " ",
        html_content
    )

    plain_text = html.unescape(plain_text)
    plain_text = re.sub(r"\s+", " ", plain_text)

    message = MIMEMultipart("alternative")

    message["Subject"] = subject
    message["From"] = EMAIL_USER
    message["To"] = EMAIL_USER

    message.attach(
        MIMEText(
            plain_text,
            "plain",
            "utf-8"
        )
    )

    message.attach(
        MIMEText(
            html_content,
            "html",
            "utf-8"
        )
    )

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465
    ) as server:

        server.login(
            EMAIL_USER,
            EMAIL_APP_PASSWORD
        )

        server.sendmail(
            EMAIL_USER,
            EMAIL_USER,
            message.as_string()
        )

    logger.info("이메일 발송 완료")


# ============================================================
# 8. 예외 상황용 원문 이메일
# ============================================================

def send_fallback_email(articles, error_message):
    today = datetime.now(KST).strftime("%Y-%m-%d")

    sections = [
        "<h1>Daily Investment Briefing</h1>",
        f"<p>{today}</p>",
        "<h2>AI 분석에 실패했습니다.</h2>",
        f"<p>{safe_text(error_message)}</p>",
        "<p>수집한 원문 기사 목록을 전달합니다.</p>",
        "<hr>"
    ]

    for article in articles:
        url = html.escape(article["url"], quote=True)

        sections.append(f"""
        <h3>
        <a href="{url}">{safe_text(article["title"])}</a>
        </h3>
        <p>
        출처: {safe_text(article["source"])}<br>
        발행: {safe_text(article["published"])}
        </p>
        <p>{safe_text(article["summary"])}</p>
        <hr>
        """)

    html_content = f"""
    <html>
    <body style="font-family:Arial,sans-serif;line-height:1.6;padding:20px;">
    {''.join(sections)}
    </body>
    </html>
    """

    send_email(
        f"[투자 브리핑] {today} - AI 분석 실패 / 원문 전달",
        html_content
    )


# ============================================================
# 9. 메인 실행
# ============================================================

def main():
    logger.info("=" * 50)
    logger.info("Daily Investment Briefing 시작")
    logger.info("모델: %s", GEMINI_MODEL)
    logger.info("=" * 50)

    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        raise ValueError("이메일 환경변수를 확인하세요.")

    processed_urls = load_history()

    articles = fetch_latest_news(processed_urls)

    today = datetime.now(KST).strftime("%Y-%m-%d")

    # 신규 기사가 없는 경우
    if not articles:
        logger.info("신규 기사가 없습니다.")

        send_email(
            f"[투자 브리핑] {today} - 신규 기사 없음",
            """
            <html>
            <body>
            <h2>Daily Investment Briefing</h2>
            <p>최근 72시간 이내에 새롭게 수집된 기사가 없습니다.</p>
            </body>
            </html>
            """
        )

        return

    # Gemini 분석
    try:
        analysis = analyze_news_with_gemini(articles)

        html_content = build_email_html(
            analysis,
            articles
        )

        send_email(
            f"[투자 브리핑] {today} - {len(articles)}개 기사 분석",
            html_content
        )

        # 이메일 발송 성공 후에만 처리 이력 저장
        for article in articles:
            processed_urls.add(article["url"])

        save_history(processed_urls)

        logger.info("분석 및 발송 완료")

    except Exception as error:
        logger.exception("Gemini 분석 또는 이메일 발송 실패")

        # 원문 전달 시도
        try:
            send_fallback_email(
                articles,
                str(error)
            )

            logger.info("원문 전달 이메일 발송 완료")

        except Exception:
            logger.exception("원문 이메일 발송도 실패")

        # 처리 이력에는 저장하지 않음.
        # 다음 실행에서 다시 분석할 수 있도록 유지.
        raise


if __name__ == "__main__":
    main()
