import os
import time
import re
import uuid
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file, session
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import quote_plus
import io
import threading

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here-change-in-production')

# 세션별 크롤링 결과 저장 (session_id -> {results, status, stop_flag})
user_sessions = {}

# 제외할 사이트 목록 (정확한 도메인 매칭)
EXCLUDE_DOMAINS = [
    # 네이버/다음/카카오
    "naver.com", "naver.me", "daum.net", "kakao.com",
    
    # 블로그/커뮤니티 (정확한 도메인)
    "tistory.com", "blog.me", "brunch.co.kr", "medium.com", 
    "velog.io", "notion.so", "notion.site",
    "dcinside.com", "clien.net", "ruliweb.com", "fmkorea.com",
    
    # 채용사이트
    "saramin.co.kr", "jobkorea.co.kr", "incruit.com", 
    "wanted.co.kr", "jobplanet.co.kr", "catch.co.kr",
    
    # 쇼핑몰
    "gmarket.co.kr", "11st.co.kr", "coupang.com", "auction.co.kr",
    "aliexpress.com", "amazon.com", "ebay.com",
    
    # 해외 도매
    "alibaba.com", "made-in-china.com", "globalsources.com",
    
    # 소셜미디어
    "youtube.com", "facebook.com", "instagram.com", 
    "twitter.com", "linkedin.com",
    
    # 위키/백과
    "wikipedia.org", "namu.wiki", "terms.naver.com",
    
    # 정부/공공
    "go.kr",
]


def is_valid_company_url(url):
    """회사 웹사이트로 적합한 URL인지 확인 (더 정교한 필터링)"""
    url_lower = url.lower()
    
    # 정확한 도메인 매칭
    for domain in EXCLUDE_DOMAINS:
        # 도메인이 URL에 정확히 포함되어 있는지 확인
        if domain in url_lower:
            return False
    
    # 블로그 패턴 제외 (blog가 URL 경로에 있는 경우)
    if "/blog/" in url_lower or "/cafe/" in url_lower:
        return False
    
    # 뉴스 기사 패턴 제외 (article, news 경로)
    if "/article/" in url_lower or "/news/" in url_lower:
        return False
    
    return True


def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--single-process")
    chrome_options.add_argument("--disable-setuid-sandbox")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Railway/Docker 환경
    if os.path.exists('/usr/bin/google-chrome'):
        chrome_options.binary_location = '/usr/bin/google-chrome'
    
    # webdriver-manager가 자동으로 ChromeDriver 설치
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver


def get_naver_links(driver, keyword, pages=5):
    links = []
    
    for page in range(1, pages + 1):
        url = f"https://search.naver.com/search.naver?where=web&query={quote_plus(keyword)}&page={page}"
        driver.get(url)
        time.sleep(2)
        
        # 네이버 웹 검색 결과의 다양한 선택자
        selectors = [
            # 웹 검색 결과 제목 링크
            "a.link_tit",
            "div.total_tit a",
            "a.total_tit", 
            "a.title_link",
            # 웹문서 결과
            "div.web_item a.link",
            "div.lst_view a",
            "div.api_txt_lines a",
            # 통합검색 결과
            "div.total_wrap a",
            "li.bx a",
            # 일반 외부 링크
            "a[href^='http']:not([href*='naver.com']):not([href*='search.naver'])",
        ]
        
        for selector in selectors:
            try:
                results = driver.find_elements(By.CSS_SELECTOR, selector)
                for res in results:
                    href = res.get_attribute("href")
                    if href and href.startswith("http") and is_valid_company_url(href):
                        # 네이버 내부 링크 한번 더 체크
                        if "naver.com" not in href and "search.naver" not in href:
                            links.append(href)
            except:
                pass
        
        # 페이지 스크롤해서 더 많은 결과 로드
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
        except:
            pass
    
    return list(set(links))


def extract_company_info(driver, url):
    info = {
        "URL": url,
        "사이트명": "",
        "회사명": "",
        "대표자명": "",
        "회사주소": "",
        "이메일": ""
    }
    
    try:
        driver.set_page_load_timeout(10)
        driver.get(url)
        time.sleep(1.5)
        
        info["사이트명"] = driver.title.strip() if driver.title else ""
        body_text = driver.find_element(By.TAG_NAME, "body").text
        
        # 이메일 추출
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        found_emails = re.findall(email_pattern, body_text)
        real_emails = [e for e in found_emails if not e.lower().endswith(('.png', '.jpg', '.gif', '.svg', '.jpeg', '.webp'))]
        info["이메일"] = ", ".join(set(real_emails))
        
        # 회사명 추출
        company_patterns = [
            r'(?:회사명|상호|법인명|업체명|기업명)\s*[:\s]\s*([^\n\r,|(]{2,30})',
            r'\(주\)\s*([가-힣a-zA-Z0-9\s]{2,20})',
            r'([가-힣]{2,15}(?:주식회사|㈜|\(주\)))',
            r'((?:주식회사|㈜)\s*[가-힣a-zA-Z0-9]{2,15})',
        ]
        for pattern in company_patterns:
            match = re.search(pattern, body_text)
            if match:
                info["회사명"] = match.group(1).strip()
                break
        
        # 대표자명 추출
        ceo_patterns = [
            r'(?:대표자?|대표이사|CEO|대표자명)\s*[:\s]\s*([가-힣]{2,5})',
            r'대표이사\s*([가-힣]{2,5})',
        ]
        for pattern in ceo_patterns:
            match = re.search(pattern, body_text, re.IGNORECASE)
            if match:
                info["대표자명"] = match.group(1).strip()
                break
        
        # 회사 주소 추출
        address_patterns = [
            r'(?:주소|소재지|사업장\s*소재지|본사)\s*[:\s]\s*([^\n\r]{10,80})',
            r'((?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)[^\n\r]{10,70})',
        ]
        for pattern in address_patterns:
            match = re.search(pattern, body_text)
            if match:
                addr = match.group(1).strip()[:80]
                info["회사주소"] = addr
                break
        
        return info
    except:
        return info


def run_crawling(keywords, session_id, max_count=0, search_pages=5):
    global user_sessions
    
    user_sessions[session_id] = {
        "results": [],
        "status": {"running": True, "progress": "크롤링 시작...", "completed": False},
        "stop_flag": False
    }
    
    driver = setup_driver()
    
    try:
        # URL 수집
        all_urls = []
        for i, keyword in enumerate(keywords):
            # 정지 버튼 체크
            if user_sessions[session_id]["stop_flag"]:
                break
                
            if keyword.strip():
                user_sessions[session_id]["status"]["progress"] = f"'{keyword}' 검색 중... ({i+1}/{len(keywords)})"
                urls = get_naver_links(driver, keyword.strip(), pages=search_pages)
                all_urls.extend(urls)
        
        target_urls = list(set(all_urls))
        total_sites = len(target_urls)
        user_sessions[session_id]["status"]["progress"] = f"총 {total_sites}개 사이트 발견. 정보 수집 중..."
        
        # 중복 체크용 세트
        seen_urls = set()
        seen_companies = set()
        seen_emails = set()
        
        # 회사 정보 수집
        for i, url in enumerate(target_urls):
            # 정지 버튼 체크
            if user_sessions[session_id]["stop_flag"]:
                user_sessions[session_id]["status"]["progress"] = f"정지됨! {len(user_sessions[session_id]['results'])}개 회사 정보 수집"
                break
            
            # 개수 제한 체크
            if max_count > 0 and len(user_sessions[session_id]["results"]) >= max_count:
                user_sessions[session_id]["status"]["progress"] = f"완료! {max_count}개 도달 (목표 달성)"
                break
            
            collected = len(user_sessions[session_id]["results"])
            target_text = f"/{max_count}" if max_count > 0 else ""
            user_sessions[session_id]["status"]["progress"] = f"정보 수집 중... ({i+1}/{total_sites}) - 수집: {collected}{target_text}개"
            
            info = extract_company_info(driver, url)
            
            if info["이메일"]:
                # 중복 체크: URL, 회사명, 이메일 기준
                url_base = url.split('?')[0].rstrip('/')  # 쿼리 파라미터 제거
                company_name = info["회사명"].strip() if info["회사명"] else ""
                email_key = info["이메일"].lower().strip()
                
                is_duplicate = False
                
                # URL 중복 체크
                if url_base in seen_urls:
                    is_duplicate = True
                
                # 회사명 중복 체크 (회사명이 있는 경우만)
                if company_name and company_name in seen_companies:
                    is_duplicate = True
                
                # 이메일 중복 체크
                if email_key in seen_emails:
                    is_duplicate = True
                
                if not is_duplicate:
                    seen_urls.add(url_base)
                    if company_name:
                        seen_companies.add(company_name)
                    seen_emails.add(email_key)
                    user_sessions[session_id]["results"].append(info)
        
        if not user_sessions[session_id]["stop_flag"]:
            user_sessions[session_id]["status"]["progress"] = f"완료! {len(user_sessions[session_id]['results'])}개 회사 정보 수집"
        
        user_sessions[session_id]["status"]["completed"] = True
        
    finally:
        driver.quit()
        user_sessions[session_id]["status"]["running"] = False


@app.route('/')
def index():
    # 세션 ID가 없으면 생성
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    return render_template('index.html')


@app.route('/crawl', methods=['POST'])
def crawl():
    global user_sessions
    
    # 세션 ID 확인
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    session_id = session['session_id']
    
    # 이 사용자가 이미 크롤링 중인지 확인
    if session_id in user_sessions and user_sessions[session_id]["status"]["running"]:
        return jsonify({"error": "이미 크롤링이 진행 중입니다."}), 400
    
    data = request.json
    keywords = data.get('keywords', [])
    max_count = data.get('maxCount', 0)  # 0이면 제한 없음
    search_pages = data.get('searchPages', 5)  # 기본 5페이지
    
    if not keywords or all(k.strip() == '' for k in keywords):
        return jsonify({"error": "검색어를 입력해주세요."}), 400
    
    # 백그라운드에서 크롤링 실행
    thread = threading.Thread(target=run_crawling, args=(keywords, session_id, max_count, search_pages))
    thread.start()
    
    return jsonify({"message": "크롤링을 시작합니다."})


@app.route('/stop', methods=['POST'])
def stop():
    session_id = session.get('session_id')
    
    if not session_id or session_id not in user_sessions:
        return jsonify({"error": "진행 중인 크롤링이 없습니다."}), 400
    
    if not user_sessions[session_id]["status"]["running"]:
        return jsonify({"error": "크롤링이 실행 중이 아닙니다."}), 400
    
    # 정지 플래그 설정
    user_sessions[session_id]["stop_flag"] = True
    
    return jsonify({"message": "크롤링을 정지합니다."})


@app.route('/status')
def status():
    session_id = session.get('session_id')
    
    if not session_id or session_id not in user_sessions:
        return jsonify({
            "running": False,
            "progress": "",
            "completed": False,
            "count": 0
        })
    
    user_data = user_sessions[session_id]
    return jsonify({
        "running": user_data["status"]["running"],
        "progress": user_data["status"]["progress"],
        "completed": user_data["status"]["completed"],
        "count": len(user_data["results"])
    })


@app.route('/results')
def results():
    session_id = session.get('session_id')
    
    if not session_id or session_id not in user_sessions:
        return jsonify([])
    
    return jsonify(user_sessions[session_id]["results"])


@app.route('/download')
def download():
    session_id = session.get('session_id')
    
    if not session_id or session_id not in user_sessions:
        return "다운로드할 데이터가 없습니다.", 400
    
    results_data = user_sessions[session_id]["results"]
    
    if not results_data:
        return "다운로드할 데이터가 없습니다.", 400
    
    df = pd.DataFrame(results_data)
    columns = ["회사명", "이메일", "대표자명", "회사주소", "URL"]
    df = df[columns]
    
    output = io.BytesIO()
    df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='회사정보_리스트.xlsx'
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('RAILWAY_ENVIRONMENT') is None
    app.run(host='0.0.0.0', port=port, debug=debug)

