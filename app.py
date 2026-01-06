import os
import time
import re
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import quote_plus
import io
import threading

app = Flask(__name__)

# 크롤링 결과 저장
crawling_results = []
crawling_status = {"running": False, "progress": "", "completed": False}

# 제외할 사이트 목록
EXCLUDE_DOMAINS = [
    "naver.com", "naver.me",
    "news", "snmnews", "chosun", "joongang", "hani", "donga", "hankyung",
    "mk.co.kr", "mt.co.kr", "edaily", "newsis", "yonhap", "yna.co.kr", "khan",
    "tistory", "blog", "brunch", "medium.com", "velog", "notion.so",
    "cafe.daum", "dcinside", "clien", "ruliweb", "fmkorea",
    "saramin", "jobkorea", "job.gg.go.kr", "incruit", "wanted", "jobaba",
    "alba", "work.go.kr", "catch.co.kr", "superookie", "workieum",
    "gmarket", "11st", "coupang", "auction", "interpark", "wemakeprice",
    "tmon", "ssg.com", "lotte", "shinsegae", "hmall", "gsshop",
    "alibaba", "aliexpress", "amazon", "ebay", "taobao",
    "mfgrobots", "made-in-china", "globalsources",
    "firstmold", "djmolding", "sanonchina", "yujebearing",
    "custom-plastic-molds", "rjcmold", "formlabs", "boyiprototyping",
    "juliertech", ".cn", ".com.cn",
    "wikipedia", "namu.wiki", "openstreetmap", "google.com", "youtube",
    "facebook", "instagram", "twitter", "linkedin",
    "kisti.re.kr", "scienceon",
]


def is_valid_company_url(url):
    url_lower = url.lower()
    for domain in EXCLUDE_DOMAINS:
        if domain in url_lower:
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
        
        selectors = [
            "a.link_tit", "div.total_tit a", "a.total_tit",
            "div.api_txt_lines a", "a[href*='http']:not([href*='naver'])",
        ]
        
        for selector in selectors:
            try:
                results = driver.find_elements(By.CSS_SELECTOR, selector)
                for res in results:
                    href = res.get_attribute("href")
                    if href and href.startswith("http") and is_valid_company_url(href):
                        links.append(href)
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


def run_crawling(keywords):
    global crawling_results, crawling_status
    
    crawling_results = []
    crawling_status = {"running": True, "progress": "크롤링 시작...", "completed": False}
    
    driver = setup_driver()
    
    try:
        # URL 수집
        all_urls = []
        for i, keyword in enumerate(keywords):
            if keyword.strip():
                crawling_status["progress"] = f"'{keyword}' 검색 중... ({i+1}/{len(keywords)})"
                urls = get_naver_links(driver, keyword.strip(), pages=3)
                all_urls.extend(urls)
        
        target_urls = list(set(all_urls))
        crawling_status["progress"] = f"총 {len(target_urls)}개 사이트 발견. 정보 수집 중..."
        
        # 회사 정보 수집
        for i, url in enumerate(target_urls):
            crawling_status["progress"] = f"정보 수집 중... ({i+1}/{len(target_urls)})"
            info = extract_company_info(driver, url)
            if info["이메일"]:
                crawling_results.append(info)
        
        crawling_status["progress"] = f"완료! {len(crawling_results)}개 회사 정보 수집"
        crawling_status["completed"] = True
        
    finally:
        driver.quit()
        crawling_status["running"] = False


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/crawl', methods=['POST'])
def crawl():
    global crawling_status
    
    if crawling_status["running"]:
        return jsonify({"error": "이미 크롤링이 진행 중입니다."}), 400
    
    data = request.json
    keywords = data.get('keywords', [])
    
    if not keywords or all(k.strip() == '' for k in keywords):
        return jsonify({"error": "검색어를 입력해주세요."}), 400
    
    # 백그라운드에서 크롤링 실행
    thread = threading.Thread(target=run_crawling, args=(keywords,))
    thread.start()
    
    return jsonify({"message": "크롤링을 시작합니다."})


@app.route('/status')
def status():
    return jsonify({
        "running": crawling_status["running"],
        "progress": crawling_status["progress"],
        "completed": crawling_status["completed"],
        "count": len(crawling_results)
    })


@app.route('/results')
def results():
    return jsonify(crawling_results)


@app.route('/download')
def download():
    if not crawling_results:
        return "다운로드할 데이터가 없습니다.", 400
    
    df = pd.DataFrame(crawling_results)
    columns = ["회사명", "사이트명", "이메일", "대표자명", "회사주소", "URL"]
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

