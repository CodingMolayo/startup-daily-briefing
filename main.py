
import os
import re
import json
import html
import time
import calendar
import logging
import smtplib
import feedparser

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from groq import Groq


# ==========================================
# 1. 기본 설정
# ==========================================

KST = ZoneInfo("Asia/Seoul")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")

GROQ_MODEL = os.environ.get(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

MAX_AGE_HOURS = 72
MAX_ARTICLES = 40
ARTICLES_PER_FEED = 8
HISTORY_FILE = "processed_articles.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


# ==========================================
# 2. 뉴스 수집원
# ==========================================

def google_news_url(query, language="ko", country="KR"):
    """Google News RSS 검색 주소 생성"""

    if language == "en":
        return (
            "https://news.google.com/rss/search?q="
            + quote_plus(query + " when:3d")
            + "&hl=en-US&gl=US&ceid=US:en"
        )

    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query + " when:3d")
        + "&hl=ko&gl=KR&ceid=KR:ko"
    )


RSS_FEEDS = [
    # 국내 전문 언론
    (
        "벤처스퀘어 뉴스",
        "https://www.venturesquare.net/rss/news"
    ),
    (
        "벤처스퀘어 인터뷰",
        "https://www.venturesquare.net/rss/interview"
    ),
    (
        "벤처스퀘어 트렌드",
        "https://www.venturesquare.net/rss/trend"
    ),

    # 관심 투자사
    (
        "매쉬업벤처스 관련 뉴스",
        google_news_url('"매쉬업벤처스" 투자 OR 포트폴리오')
    ),
    (
        "소풍벤처스 관련 뉴스",
        google_news_url('"소풍벤처스" 투자 OR 임팩트 OR 포트폴리오')
    ),
    (
        "MYSC 관련 뉴스",
        google_news_url('"MYSC" 임팩트 투자 OR 포트폴리오')
    ),
    (
        "블루포인트 관련 뉴스",
        google_news_url('"블루포인트파트너스" 투자 OR 포트폴리오')
    ),
    (
        "알토스벤처스 관련 뉴스",
        google_news_url('"알토스벤처스" 투자 OR 포트폴리오')
    ),
    (
        "Y Combinator 관련 뉴스",
        google_news_url(
            '"Y Combinator" startup investment portfolio',
            language="en"
        )
    ),

    # 관심 산업
    (
        "환경 및 기후테크",
        google_news_url("기후테크 환경 스타트업 투자 유치")
    ),
    (
        "핀테크",
        google_news_url("핀테크 금융 스타트업 투자 유치")
    ),
    (
        "임팩트 투자",
        google_news_url("임팩트 투자 소셜벤처 투자 유치")
    ),
    (
        "인프라",
        google_news_url("인프라 에너지 물류 스타트업 투자")
    ),
    (
        "콘텐츠",
        google_news_url("콘텐츠 플랫폼 창작자 스타트업 투자")
    ),

    # 국내외 투자 이벤트
    (
        "스타트업 투자 유치",
        google_news_url("스타트업 시리즈 A B C 투자 유치")
    ),
    (
        "글로벌 임팩트 투자",
        google_news_url(
            "impact investing climate tech fintech startup funding",
            language="en"
        )
    )
]


# ==========================================
# 3. 뉴스 데이터 정리
# ==========================================

def clean_text(text):
    """HTML 태그 및 불필요한 공백 제거"""

    text = re.sub(r"<[^>]+>", " ", str(text))
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def get_entry_date(entry):
    """기사 발행일을 UTC 기준으로 변환"""

    parsed = (
        entry.get("published_parsed")
        or entry.get("updated_parsed")
    )

    if not parsed:
        return None

    timestamp = calendar.timegm(parsed)

    return datetime.fromtimestamp(
        timestamp,
        timezone.utc
    )


def fetch_latest_news(processed_urls):
    """RSS에서 최신 뉴스 수집 및 중복 제거"""

    articles = []
    seen_urls = set()
    seen_titles = set()

    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=MAX_AGE_HOURS
    )

    for source_name, url in RSS_FEEDS:

        try:
            logging.info("뉴스 수집: %s", source_name)

            feed = feedparser.parse(url)

            for entry in feed.entries[:ARTICLES_PER_FEED]:

                link = entry.get("link", "").strip()
                title = clean_text(entry.get("title", ""))

                if not link or not title:
                    continue

                if link in processed_urls or link in seen_urls:
                    continue

                normalized_title = re.sub(
                    r"[^가-힣a-zA-Z0-9]",
                    "",
                    title
                ).lower()

                if normalized_title in seen_titles:
                    continue

                published = get_entry_date(entry)

                # 날짜가 확인되지 않으면 분석 대상에서 제외
                if published is None or published < cutoff:
                    continue

                summary = clean_text(
                    entry.get("summary", "")
                )

                if not summary and entry.get("content"):
                    summary = clean_text(
                        entry.content[0].get("value", "")
                    )

                article_id = f"A{len(articles) + 1:02d}"

                articles.append({
                    "id": article_id,
                    "source": source_name,
                    "title": title,
                    "url": link,
                    "published": published.isoformat(),
                    "summary": summary[:700]
                })

                seen_urls.add(link)
                seen_titles.add(normalized_title)

        except Exception as error:
            logging.warning(
                "%s 수집 실패: %s",
                source_name,
                error
            )

    articles.sort(
        key=lambda x: x["published"],
        reverse=True
    )

    articles = articles[:MAX_ARTICLES]

    # ID를 최종 목록 기준으로 다시 정렬
    for index, article in enumerate(articles, 1):
        article["id"] = f"A{index:02d}"

    logging.info("최종 수집 기사: %s개", len(articles))

    return articles


# ==========================================
# 4. 기존 수집 기록 관리
# ==========================================

def load_history():

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        return set(data.get("processed_urls", []))

    except FileNotFoundError:
        return set()

    except Exception as error:
        logging.warning("기존 기록을 읽지 못했습니다: %s", error)
        return set()


def save_history(processed_urls):

    data = {
        "processed_urls": sorted(
            list(processed_urls)[-500:]
        )
    }

    with open(HISTORY_FILE, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )


# ==========================================
# 5. AI 투자 분석
# ==========================================

def analyze_with_groq(articles):

    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY가 등록되지 않았습니다.")

    client = Groq(api_key=GROQ_API_KEY)

    news_text = json.dumps(
        articles,
        ensure_ascii=False,
        indent=2
    )

    prompt = f"""
너는 초기 스타트업 투자, 임팩트 투자, 엔젤투자에
전문성을 가진 투자 리서치 애널리스트다.

다음은 최근 72시간 동안 수집한 뉴스 데이터다.

[뉴스 데이터]
{news_text}

[사용자의 투자 배경]

- 창업 실패 경험: 콘텐츠 큐레이팅 플랫폼
- 액셀러레이터 배치 참여 경험
- 엔젤투자평가사 2급
- 스타트업 투자 실무 지식은 입문 수준
- 관심 산업: 환경, 핀테크, 임팩트 투자, 인프라, 콘텐츠
- 관심 투자사: 매쉬업벤처스, 소풍벤처스,
  MYSC, 블루포인트파트너스, 알토스벤처스,
  Y Combinator

[투자 철학]

자본주의의 구조적 문제를 인식하며,
기존 시스템의 문제에 균열을 만들 수 있는
기업과 사업 모델에 관심이 있다.

다음 관점으로 분석한다.

1. 경제적 지속 가능성
2. 사회적·환경적 임팩트
3. 기존 경제 구조의 변화 가능성
4. 엔젤투자자 관점의 투자 리스크

[분석 원칙]

- 기사에 없는 매출, 투자금, 고객 수 등을 만들지 마라.
- 사실과 분석 가설을 반드시 구분하라.
- 투자 유치 배경은 확정적으로 표현하지 마라.
- 공개된 정보가 부족하면 '확인 필요'라고 표시하라.
- 투자사와 포트폴리오 기업의 관계를 정확하게 구분하라.
- 단순 뉴스 요약보다 투자 관점의 해석에 집중하라.
- 사회적 가치가 있다는 이유만으로 좋은 기업이라고 평가하지 마라.
- 임팩트 워싱 가능성을 검토하라.
- 각 분석에 사용한 뉴스의 ID를 반드시 표시하라.
- 기사에 없는 사실을 근거로 삼지 마라.
- 수익성과 임팩트 사이의 충돌 가능성도 분석하라.

[요청 결과]

다음 JSON 구조로만 출력하라.

{{
  "daily_summary": "오늘 시장의 핵심 변화",

  "investment_events": [
    {{
      "title": "기업명 및 투자 이벤트",
      "category": "투자 유치 / 후속 투자 / 산업 동향",
      "source_ids": ["A01"],
      "confirmed_facts": [
        "기사에서 확인된 사실"
      ],
      "investment_hypotheses": [
        "투자 집행 배경에 대한 가설"
      ],
      "impact_analysis": [
        "사회적·환경적 임팩트 분석"
      ],
      "risks": [
        "엔젤투자자 관점의 리스크"
      ],
      "unknowns": [
        "추가로 확인해야 할 정보"
      ],
      "relevance_score": 5
    }}
  ],

  "market_signals": [
    "오늘 발견한 시장 변화"
  ],

  "linkedin_ideas": [
    {{
      "headline": "포스팅 소재 제목",
      "angle": "어떤 관점으로 접근할 것인가",
      "question": "독자에게 던질 질문",
      "source_ids": ["A01"]
    }}
  ],

  "learning_notes": [
    "오늘 새롭게 알아두면 좋은 투자 관련 개념"
  ]
}}

[작성 기준]

- 투자 이벤트는 최대 5개
- 링크드인 소재는 3개
- 시장 시그널은 최대 5개
- 학습 노트는 최대 3개
- relevance_score는 1~5점
- source_ids는 반드시 제공된 기사 ID만 사용
- 기사 내용이 불충분하면 억지로 분석하지 말 것
- 유효한 JSON 이외의 문장은 출력하지 말 것
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "너는 신중하고 비판적인 임팩트 투자 리서치 "
                    "애널리스트다. 근거 없는 사실을 만들지 않는다."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        max_completion_tokens=7000
    )

    result = response.choices[0].message.content

    if not result:
        raise ValueError("AI 응답이 비어 있습니다.")

    # JSON 이외의 설명이나 코드 펜스가 붙었을 경우 처리
    result = result.strip()
    result = re.sub(r"^```(?:json)?\s*", "", result)
    result = re.sub(r"\s*```$", "", result)

    start = result.find("{")
    end = result.rfind("}")

    if start < 0 or end < start:
        raise ValueError("AI 응답에서 JSON을 찾을 수 없습니다.")

    return json.loads(result[start:end + 1])


# ==========================================
# 6. HTML 이메일 구성
# ==========================================

def safe_text(value):
    return html.escape(str(value or ""))


def get_sources(source_ids, articles):

    article_map = {
        article["id"]: article
        for article in articles
    }

    sources = []

    for source_id in source_ids or []:

        article = article_map.get(source_id)

        if article:
            sources.append(article)

    return sources


def render_source_links(source_ids, articles):

    sources = get_sources(source_ids, articles)

    if not sources:
        return "<p>참고 기사 확인 필요</p>"

    result = []

    for article in sources:

        result.append(
            '<p style="margin:5px 0;font-size:13px;">'
            f'<a href="{safe_text(article["url"])}">'
            f'{safe_text(article["title"])}</a>'
            f' <span style="color:#888;">'
            f'({safe_text(article["source"])})</span></p>'
        )

    return "".join(result)


def render_list(items):

    if not isinstance(items, list) or not items:
        return "<p>별도 분석 내용 없음</p>"

    return (
        "<ul>"
        + "".join(
            f"<li>{safe_text(item)}</li>"
            for item in items
        )
        + "</ul>"
    )


def build_report(analysis, articles):

    summary = safe_text(
        analysis.get("daily_summary", "요약 정보 없음")
    )

    html_body = f"""
    <h2>오늘의 시장 요약</h2>
    <p>{summary}</p>
    """

    events = analysis.get("investment_events", [])

    html_body += "<h2>투자 이벤트 및 기업 분석</h2>"

    if not events:
        html_body += "<p>주요 투자 이벤트가 확인되지 않았습니다.</p>"

    for index, event in enumerate(events, 1):

        score = event.get("relevance_score", 0)

        try:
            score = max(1, min(5, int(score)))
        except (TypeError, ValueError):
            score = "미평가"

        html_body += f"""
        <div style="padding:16px 0;border-bottom:1px solid #ddd;">

        <h3>{index}. {safe_text(event.get("title"))}</h3>

        <p>
        <strong>분류:</strong>
        {safe_text(event.get("category"))}
        &nbsp; | &nbsp;
        <strong>관련성:</strong> {score}/5
        </p>

        <h4>확인된 사실</h4>
        {render_list(event.get("confirmed_facts"))}

        <h4>투자 배경에 대한 분석 가설</h4>
        {render_list(event.get("investment_hypotheses"))}

        <h4>임팩트 분석</h4>
        {render_list(event.get("impact_analysis"))}

        <h4>엔젤투자자 관점의 리스크</h4>
        {render_list(event.get("risks"))}

        <h4>추가 확인 필요</h4>
        {render_list(event.get("unknowns"))}

        <h4>참고 자료</h4>
        {render_source_links(event.get("source_ids"), articles)}

        </div>
        """

    html_body += "<h2>오늘의 시장 시그널</h2>"
    html_body += render_list(
        analysis.get("market_signals")
    )

    html_body += "<h2>링크드인 포스팅 소재</h2>"

    ideas = analysis.get("linkedin_ideas", [])

    for index, idea in enumerate(ideas, 1):

        html_body += f"""
        <div style="padding:12px;background:#f5f7fa;
                    margin:10px 0;border-radius:6px;">

        <h3>{index}. {safe_text(idea.get("headline"))}</h3>

        <p><strong>접근 관점</strong></p>
        <p>{safe_text(idea.get("angle"))}</p>

        <p><strong>독자에게 던질 질문</strong></p>
        <p>{safe_text(idea.get("question"))}</p>

        <p><strong>관련 뉴스</strong></p>
        {render_source_links(idea.get("source_ids"), articles)}

        </div>
        """

    html_body += "<h2>오늘의 투자 공부</h2>"
    html_body += render_list(
        analysis.get("learning_notes")
    )

    return html_body


def build_sources_section(articles):

    result = "<h2>전체 수집 기사</h2>"

    if not articles:
        return result + "<p>새로운 기사가 없습니다.</p>"

    for article in articles:

        result += f"""
        <p style="font-size:13px;">
        <a href="{safe_text(article["url"])}">
        {safe_text(article["title"])}
        </a><br>
        <span style="color:#888;">
        {safe_text(article["source"])}
        · {safe_text(article["published"][:10])}
        </span>
        </p>
        """

    return result


# ==========================================
# 7. 이메일 발송
# ==========================================

def send_email(subject, html_content, plain_content):

    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        raise ValueError("이메일 환경변수가 등록되지 않았습니다.")

    msg = MIMEMultipart("alternative")

    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_USER
    msg["Subject"] = subject

    msg.attach(
        MIMEText(plain_content, "plain", "utf-8")
    )

    msg.attach(
        MIMEText(html_content, "html", "utf-8")
    )

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        timeout=30
    ) as server:

        server.login(
            EMAIL_USER,
            EMAIL_APP_PASSWORD
        )

        server.send_message(msg)

    logging.info("이메일 발송 완료")


# ==========================================
# 8. 프로그램 실행
# ==========================================

def main():

    logging.info("데일리 투자 리서치 시작")

    processed_urls = load_history()

    # 뉴스 수집
    articles = fetch_latest_news(processed_urls)

    today = datetime.now(KST).strftime("%Y-%m-%d")

    subject = (
        f"[Daily Impact Investment Briefing] {today}"
    )

    # 수집된 기사가 없는 경우
    if not articles:

        html_body = """
        <h2>오늘은 새로운 수집 기사가 없습니다.</h2>
        <p>최근 72시간 내 새로운 뉴스가 없거나
        기존에 처리한 기사만 확인되었습니다.</p>
        """

        send_email(
            subject,
            html_body,
            "오늘은 새로운 수집 기사가 없습니다."
        )

        return

    logging.info("AI 분석 시작")

    try:

        analysis = analyze_with_groq(articles)

        report_html = build_report(
            analysis,
            articles
        )

        analysis_success = True

        plain_content = (
            "오늘의 투자 리서치 보고서입니다. "
            "HTML 이메일로 확인해 주세요."
        )

    except Exception as error:

        logging.exception("AI 분석 실패")

        analysis_success = False

        report_html = f"""
        <h2>AI 분석에 실패했습니다.</h2>
        <p>오류: {safe_text(error)}</p>
        <p>수집된 원문 기사 목록을 아래에서 확인해 주세요.</p>
        """

        plain_content = (
            "AI 분석에 실패했습니다. "
            "아래 원문 기사 목록을 확인해 주세요."
        )

    final_html = f"""
    <div style="
        font-family:Arial,'Malgun Gothic',sans-serif;
        max-width:760px;
        margin:0 auto;
        padding:24px;
        color:#24292e;
        line-height:1.7;
    ">

        <h1 style="color:#1f497d;">
        Daily Impact Investment Briefing
        </h1>

        <p style="color:#777;">
        {today} · Personal Investment Research
        </p>

        <hr>

        {report_html}

        <hr>

        {build_sources_section(articles)}

        <hr>

        <p style="font-size:12px;color:#888;">
        Powered by GitHub Actions & Groq AI
        <br>
        개인 리서치 참고 자료이며 투자 권유가 아닙니다.
        </p>

    </div>
    """

    logging.info("이메일 발송 시작")

    send_email(
        subject,
        final_html,
        plain_content
    )

    # AI 분석과 이메일 발송이 모두 성공한 경우에만
    # 기존 처리 기록에 새로운 기사를 추가
    if analysis_success:

        for article in articles:
            processed_urls.add(article["url"])

        save_history(processed_urls)

        logging.info("기사 처리 기록 저장 완료")

    else:

        logging.warning(
            "AI 분석 실패: 다음 실행에서 재처리합니다."
        )

    logging.info("프로그램 종료")


if __name__ == "__main__":

    main()
