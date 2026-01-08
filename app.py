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
    
    # 채용사이트 (제외하지 않음 - 크롤링 대상)
    # "saramin.co.kr", "jobkorea.co.kr", "incruit.com", 
    # "wanted.co.kr", "jobplanet.co.kr", "catch.co.kr",
    
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
    
    # Railway/Docker 환경 감지
    is_railway = os.environ.get('RAILWAY_ENVIRONMENT') is not None
    is_docker = os.path.exists('/usr/bin/google-chrome')
    
    # 서버 환경(Railway/Docker)에서는 headless 모드 필수
    if is_railway or is_docker:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--single-process")
    # 로컬 환경에서는 headless 비활성화 (브라우저 창이 보이도록)
    # else:
    #     chrome_options.add_argument("--headless=new")  # 로컬에서도 headless 사용
    
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--disable-setuid-sandbox")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Railway/Docker 환경
    if is_docker:
        chrome_options.binary_location = '/usr/bin/google-chrome'
    
    # webdriver-manager가 자동으로 ChromeDriver 설치
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver


def get_naver_links(driver, keyword, pages=5, max_urls=0):
    """네이버 웹 검색에서 링크 수집 (목표 개수에 도달할 때까지 페이지 확장)"""
    links = []
    current_page = 1
    max_pages = pages * 3  # 최대 3배까지 확장 가능
    
    while current_page <= max_pages:
        # 네이버 검색 URL 형식 (사용자가 제공한 형식 사용)
        if current_page == 1:
            url = f"https://search.naver.com/search.naver?where=nexearch&sm=top_hty&fbm=0&ie=utf8&query={quote_plus(keyword)}"
        else:
            start = (current_page - 1) * 10 + 1
            url = f"https://search.naver.com/search.naver?nso=&page={current_page}&query={quote_plus(keyword)}&sm=tab_pge&ssc=tab.ur.all&start={start}"
        
        print(f"[네이버] 페이지 {current_page} 크롤링 중: {url}")
        driver.get(url)
        time.sleep(3)  # 페이지 로딩 대기
        
        page_links_count = len(links)
        
        # 1. 파워링크 광고에서 링크 추출 (가장 중요!)
        print(f"[네이버] 파워링크 광고 링크 추출 시작...")
        powerlink_selectors = [
            ".powerlink_area a",
            ".ad_powerlink a",
            ".power_link a",
            "[class*='powerlink'] a",
            "[class*='power_link'] a",
            "[id*='powerlink'] a",
            "[id*='power_link'] a",
            ".ad_area a",
            ".ad_section a",
        ]
        
        for selector in powerlink_selectors:
            try:
                powerlink_ads = driver.find_elements(By.CSS_SELECTOR, selector)
                print(f"[네이버] 파워링크 선택자 ({selector}): {len(powerlink_ads)}개 발견")
                for ad in powerlink_ads:
                    try:
                        href = ad.get_attribute("href")
                        if href and href.startswith("http"):
                            # 네이버 링크 제외
                            if "naver.com" not in href.lower() and "search.naver" not in href.lower():
                                if is_valid_company_url(href):
                                    if href not in links:
                                        links.append(href)
                                        print(f"[네이버] 파워링크 링크 추가: {href}")
                    except:
                        pass
            except:
                pass
        
        # 2. 파워링크 영역에서 URL 텍스트 직접 추출
        try:
            powerlink_areas = driver.find_elements(By.CSS_SELECTOR, ".powerlink_area, .ad_powerlink, [class*='powerlink'], [class*='power_link']")
            print(f"[네이버] 파워링크 영역 {len(powerlink_areas)}개 발견")
            for area in powerlink_areas:
                try:
                    # 영역 내의 모든 텍스트에서 URL 패턴 찾기
                    area_text = area.text
                    area_html = area.get_attribute("innerHTML") or ""
                    
                    # URL 패턴 찾기 (http:// 또는 https://로 시작)
                    url_pattern = r'https?://[^\s<>"\']+[^\s<>"\'.,;!?]'
                    found_urls = re.findall(url_pattern, area_text + " " + area_html)
                    
                    for found_url in found_urls:
                        # 네이버 링크 제외
                        if "naver.com" not in found_url.lower() and "search.naver" not in found_url.lower():
                            if is_valid_company_url(found_url):
                                if found_url not in links:
                                    links.append(found_url)
                                    print(f"[네이버] 파워링크 텍스트에서 URL 추출: {found_url}")
                except:
                    pass
        except:
            pass
        
        # 3. 네이버 웹 검색 결과의 다양한 선택자
        print(f"[네이버] 일반 검색 결과 링크 추출 시작...")
        selectors = [
            "a.link_tit", "div.total_tit a", "a.total_tit", "a.title_link",
            "div.web_item a.link", "div.lst_view a", "div.api_txt_lines a",
            "div.total_wrap a", "li.bx a",
            "a[href^='http']:not([href*='naver.com']):not([href*='search.naver'])",
        ]
        
        for selector in selectors:
            try:
                results = driver.find_elements(By.CSS_SELECTOR, selector)
                for res in results:
                    href = res.get_attribute("href")
                    if href and href.startswith("http") and is_valid_company_url(href):
                        if "naver.com" not in href and "search.naver" not in href:
                            if href not in links:
                                links.append(href)
            except:
                pass
        
        print(f"[네이버] 페이지 {current_page}에서 {len(links) - page_links_count}개 링크 발견 (총 {len(links)}개)")
        
        # 목표 개수에 도달했거나, 기본 페이지 범위를 넘었는데 새 링크가 없으면 중단
        if max_urls > 0 and len(links) >= max_urls:
            break
        
        # 기본 페이지 범위를 넘었는데 새 링크가 없으면 중단
        if current_page > pages and len(links) == page_links_count:
            break
        
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
        except:
            pass
        
        current_page += 1
    
    return list(set(links))


def get_daum_links(driver, keyword, pages=5, max_urls=0):
    """다음 웹 검색에서 링크 수집 (목표 개수에 도달할 때까지 페이지 확장)"""
    links = []
    current_page = 1
    max_pages = pages * 3  # 최대 3배까지 확장 가능
    
    while current_page <= max_pages:
        url = f"https://search.daum.net/search?w=web&q={quote_plus(keyword)}&p={current_page}"
        driver.get(url)
        time.sleep(2)
        
        page_links_count = len(links)
        
        # 다음 웹 검색 결과 선택자
        selectors = [
            "a.f_link_b",           # 웹문서 제목 링크
            "div.wrap_tit a",       # 제목 링크
            "a.link_txt",           # 텍스트 링크
            "div.c-tit a",          # 컨텐츠 제목
            "div.c-item a",         # 아이템 링크
            "a[href^='http']:not([href*='daum.net']):not([href*='kakao'])",
        ]
        
        for selector in selectors:
            try:
                results = driver.find_elements(By.CSS_SELECTOR, selector)
                for res in results:
                    href = res.get_attribute("href")
                    if href and href.startswith("http") and is_valid_company_url(href):
                        if "daum.net" not in href and "kakao.com" not in href:
                            if href not in links:
                                links.append(href)
            except:
                pass
        
        # 목표 개수에 도달했거나, 기본 페이지 범위를 넘었는데 새 링크가 없으면 중단
        if max_urls > 0 and len(links) >= max_urls:
            break
        
        # 기본 페이지 범위를 넘었는데 새 링크가 없으면 중단
        if current_page > pages and len(links) == page_links_count:
            break
        
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
        except:
            pass
        
        current_page += 1
    
    return list(set(links))


def get_saramin_company_links(driver, keyword, pages=10, max_urls=0):
    """사람인 사이트에서 회사 검색하여 회사 상세 페이지 링크 수집 (목표 개수에 도달할 때까지 페이지 확장)"""
    links = []
    current_page = 1
    max_pages = pages * 3  # 최대 3배까지 확장 가능
    
    try:
        while current_page <= max_pages:
            # 사람인 검색 URL (사용자가 제공한 형식 사용)
            url = f"https://www.saramin.co.kr/zf_user/search?search_area=main&search_done=y&search_optional_item=n&searchType=search&searchword={quote_plus(keyword)}&recruitPage={current_page}"
            print(f"[사람인] 페이지 {current_page} 크롤링 중: {url}")
            driver.get(url)
            time.sleep(3)  # 페이지 로딩 대기
            
            page_links_count = len(links)
            
            # 사람인 검색 결과에서 회사 상세 페이지 링크 찾기
            # 모든 방법을 병렬로 시도하여 최대한 많이 수집
            try:
                # 페이지가 완전히 로드될 때까지 대기
                time.sleep(3)
                
                # 페이지 소스 확인 (디버깅용)
                page_source_snippet = driver.page_source[:2000] if len(driver.page_source) > 2000 else driver.page_source
                print(f"[사람인] 페이지 소스 일부: {page_source_snippet[:500]}...")
                
                # 페이지에 "/zf_user/company"가 있는지 확인
                if "/zf_user/company" not in driver.page_source:
                    print(f"[사람인] 경고: 페이지에 '/zf_user/company' 링크가 없습니다!")
                else:
                    print(f"[사람인] 페이지에 '/zf_user/company' 링크가 있습니다.")
                
                # 방법 1: 모든 회사 관련 링크 찾기 (company-info/view 포함)
                # /zf_user/company-info/view 링크도 수집 (이 링크를 따라가면 회사 상세 페이지로 갈 수 있음)
                all_company_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/zf_user/company']")
                print(f"[사람인] 방법1: {len(all_company_links)}개 링크 발견")
                for link in all_company_links:
                    try:
                        href = link.get_attribute("href")
                        if href:
                            if href.startswith("/"):
                                href = "https://www.saramin.co.kr" + href
                            # 쿼리 파라미터는 유지 (csn 파라미터가 중요할 수 있음)
                            href_clean = href.split("#")[0].rstrip("/")
                            
                            # /zf_user/company-info/view 링크 수집 (이 링크를 따라가면 회사 상세 페이지로 갈 수 있음)
                            if "/zf_user/company-info/view" in href_clean:
                                if href_clean not in links:
                                    links.append(href_clean)
                                    print(f"[사람인] 링크 추가 (company-info/view): {href_clean}")
                            # 정확히 /zf_user/company/로 시작하는 링크도 수집
                            elif (href_clean.startswith("https://www.saramin.co.kr/zf_user/company/") or 
                                  href_clean.startswith("/zf_user/company/")):
                                # company-review, jobs는 제외하지만 company-info/view는 포함
                                if ("/zf_user/company-review" not in href_clean and
                                    "/zf_user/jobs" not in href_clean and
                                    "/zf_user/company/" in href_clean and
                                    href_clean not in links):
                                    links.append(href_clean)
                                    print(f"[사람인] 링크 추가: {href_clean}")
                                else:
                                    print(f"[사람인] 링크 제외 (잘못된 경로): {href_clean}")
                            else:
                                print(f"[사람인] 링크 제외 (형식 불일치): {href_clean}")
                    except Exception as e:
                        print(f"[사람인] 링크 처리 오류: {e}")
                        pass
                
                # 방법 2: "기업정보" 텍스트가 있는 링크 찾기 (전체 페이지에서) - 가장 중요!
                # 다양한 XPath 시도 (company-info/view 링크 포함)
                xpath_selectors = [
                    "//a[contains(text(), '기업정보')]",
                    "//a[text()='기업정보']",
                    "//button[contains(text(), '기업정보')]",
                    "//a[@title='기업정보']",
                    "//a[contains(@class, 'company_info')]",
                    "//a[contains(@class, 'btn_info')]",
                    "//a[contains(@class, 'company_popup')]",
                    "//a[contains(@href, '/zf_user/company-info/view')]",
                    "//a[contains(@href, '/zf_user/company/')]",
                ]
                
                all_company_info_links = []
                for xpath in xpath_selectors:
                    try:
                        found_links = driver.find_elements(By.XPATH, xpath)
                        print(f"[사람인] 방법2 ({xpath}): {len(found_links)}개 발견")
                        all_company_info_links.extend(found_links)
                    except Exception as e:
                        print(f"[사람인] 방법2 XPath 오류 ({xpath}): {e}")
                        pass
                
                # 중복 제거
                seen_elements = set()
                unique_company_info_links = []
                for link in all_company_info_links:
                    try:
                        elem_id = link.id
                        if elem_id not in seen_elements:
                            seen_elements.add(elem_id)
                            unique_company_info_links.append(link)
                    except:
                        unique_company_info_links.append(link)
                
                print(f"[사람인] 방법2: 총 {len(unique_company_info_links)}개 '기업정보' 링크 발견 (중복 제거 후)")
                
                for link in unique_company_info_links:
                    try:
                        href = link.get_attribute("href")
                        if not href:
                            # onclick 속성 확인
                            onclick = link.get_attribute("onclick")
                            if onclick and "/zf_user/company" in onclick:
                                url_match = re.search(r'/zf_user/company/[^\s\'"]+', onclick)
                                if url_match:
                                    href = "https://www.saramin.co.kr" + url_match.group(0)
                        
                        if href:
                            if href.startswith("/"):
                                href = "https://www.saramin.co.kr" + href
                            # 쿼리 파라미터는 유지
                            href_clean = href.split("#")[0].rstrip("/")
                            
                            # /zf_user/company-info/view 링크 수집
                            if "/zf_user/company-info/view" in href_clean:
                                if href_clean not in links:
                                    links.append(href_clean)
                                    print(f"[사람인] 링크 추가 (기업정보 - company-info/view): {href_clean}")
                            # 정확히 /zf_user/company/로 시작하는 링크도 수집
                            elif (href_clean.startswith("https://www.saramin.co.kr/zf_user/company/") or 
                                  href_clean.startswith("/zf_user/company/")):
                                if ("/zf_user/company-review" not in href_clean and
                                    "/zf_user/jobs" not in href_clean and
                                    "/zf_user/company/" in href_clean and
                                    href_clean not in links):
                                    links.append(href_clean)
                                    print(f"[사람인] 링크 추가 (기업정보): {href_clean}")
                                else:
                                    print(f"[사람인] 링크 제외 (잘못된 경로): {href_clean}")
                            else:
                                print(f"[사람인] 링크 제외 (형식 불일치): {href_clean}")
                    except Exception as e:
                        print(f"[사람인] 방법2 링크 처리 오류: {e}")
                        pass
                
                # 방법 3: 기업정보 버튼/링크 찾기 (다양한 선택자)
                company_info_selectors = [
                    "a[href*='/zf_user/company']",
                    "a.company_info",
                    ".company_info a",
                    "a[title*='기업정보']",
                    "a[aria-label*='기업정보']",
                    ".btn_company_info",
                    ".company_info_btn",
                ]
                for selector in company_info_selectors:
                    try:
                        elements = driver.find_elements(By.CSS_SELECTOR, selector)
                        for elem in elements:
                            href = elem.get_attribute("href")
                            if href:
                                if href.startswith("/"):
                                    href = "https://www.saramin.co.kr" + href
                                href_clean = href.split("?")[0].split("#")[0].rstrip("/")
                                # 정확히 /zf_user/company/로 시작하는 링크만 수집
                                if (href_clean.startswith("https://www.saramin.co.kr/zf_user/company/") or 
                                    href_clean.startswith("/zf_user/company/")):
                                    if ("/zf_user/company-info" not in href_clean and 
                                        "/zf_user/company-review" not in href_clean and
                                        "/zf_user/jobs" not in href_clean and
                                        "/zf_user/company/" in href_clean and
                                        href_clean not in links):
                                        links.append(href_clean)
                                        print(f"[사람인] 링크 추가 (선택자 {selector}): {href_clean}")
                                    else:
                                        print(f"[사람인] 링크 제외 (잘못된 경로): {href_clean}")
                    except:
                        pass
                
                # 방법 4: 각 채용 공고 항목에서 "기업정보" 버튼 찾기 (가장 중요!)
                try:
                    # 사람인 검색 결과의 채용 공고 항목 찾기 (더 많은 선택자 시도)
                    job_item_selectors = [
                        ".item_recruit",
                        ".recruit_item", 
                        ".job_item", 
                        ".list_item",
                        "[class*='item_recruit']",
                        "[class*='recruit_item']",
                        "[class*='list_item']",
                        "div[class*='item']",
                        "li[class*='item']",
                    ]
                    
                    all_job_items = []
                    for selector in job_item_selectors:
                        try:
                            items = driver.find_elements(By.CSS_SELECTOR, selector)
                            all_job_items.extend(items)
                        except:
                            pass
                    
                    # 중복 제거
                    seen_items = set()
                    unique_job_items = []
                    for item in all_job_items:
                        try:
                            item_id = item.id
                            if item_id not in seen_items:
                                seen_items.add(item_id)
                                unique_job_items.append(item)
                        except:
                            unique_job_items.append(item)
                    
                    print(f"[사람인] 방법4: {len(unique_job_items)}개 채용 공고 항목 발견")
                    
                    for idx, item in enumerate(unique_job_items):
                        try:
                            # 각 항목에서 "기업정보" 버튼/링크 찾기
                            # 방법 4-1: "기업정보" 텍스트가 있는 링크 찾기
                            company_info_buttons = item.find_elements(By.XPATH, ".//a[contains(text(), '기업정보')]")
                            
                            # 방법 4-2: href에 /zf_user/company 또는 /zf_user/company-info/view가 있는 링크 찾기
                            if not company_info_buttons:
                                company_info_buttons = item.find_elements(By.CSS_SELECTOR, "a[href*='/zf_user/company'], a[href*='/zf_user/company-info/view']")
                            
                            # 방법 4-2-1: btn_info 클래스가 있는 링크 찾기 (사용자가 제공한 HTML 구조)
                            if not company_info_buttons:
                                company_info_buttons = item.find_elements(By.CSS_SELECTOR, "a.btn_info, a.company_popup, .area_corp_info a")
                            
                            # 방법 4-3: 버튼 클래스가 있는 링크 찾기
                            if not company_info_buttons:
                                company_info_buttons = item.find_elements(By.CSS_SELECTOR, "a.btn, button, .btn_company_info, .company_info_btn")
                            
                            print(f"[사람인] 항목 {idx+1}: {len(company_info_buttons)}개 '기업정보' 버튼 발견")
                            
                            for button in company_info_buttons:
                                try:
                                    href = button.get_attribute("href")
                                    if not href:
                                        # onclick 속성에서 링크 추출 시도
                                        onclick = button.get_attribute("onclick")
                                        if onclick and "/zf_user/company" in onclick:
                                            # onclick에서 URL 추출
                                            import re
                                            url_match = re.search(r'/zf_user/company/[^\s\'"]+', onclick)
                                            if url_match:
                                                href = "https://www.saramin.co.kr" + url_match.group(0)
                                    
                                    if href:
                                        if href.startswith("/"):
                                            href = "https://www.saramin.co.kr" + href
                                        # 쿼리 파라미터는 유지
                                        href_clean = href.split("#")[0].rstrip("/")
                                        
                                        # /zf_user/company-info/view 링크 수집
                                        if "/zf_user/company-info/view" in href_clean:
                                            if href_clean not in links:
                                                links.append(href_clean)
                                                print(f"[사람인] 링크 추가 (항목 {idx+1} '기업정보' 버튼 - company-info/view): {href_clean}")
                                        # 정확히 /zf_user/company/로 시작하는 링크도 수집
                                        elif (href_clean.startswith("https://www.saramin.co.kr/zf_user/company/") or 
                                              href_clean.startswith("/zf_user/company/")):
                                            if ("/zf_user/company-review" not in href_clean and
                                                "/zf_user/jobs" not in href_clean and
                                                "/zf_user/company/" in href_clean and
                                                href_clean not in links):
                                                links.append(href_clean)
                                                print(f"[사람인] 링크 추가 (항목 {idx+1} '기업정보' 버튼): {href_clean}")
                                            else:
                                                print(f"[사람인] 링크 제외 (잘못된 경로): {href_clean}")
                                        else:
                                            print(f"[사람인] 링크 제외 (형식 불일치): {href_clean}")
                                except Exception as e:
                                    print(f"[사람인] 항목 {idx+1} 버튼 처리 오류: {e}")
                                    pass
                        except Exception as e:
                            print(f"[사람인] 항목 {idx+1} 처리 오류: {e}")
                            pass
                except Exception as e:
                    print(f"[사람인] 방법4 오류: {e}")
                    import traceback
                    print(traceback.format_exc())
                    pass
                
                # 방법 5: 사람인 검색 결과의 실제 구조에 맞는 선택자 추가
                try:
                    # 사람인 검색 결과 페이지의 실제 구조 확인
                    page_html = driver.page_source
                    if "/zf_user/company" in page_html:
                        # 더 구체적인 선택자로 시도 (채용 공고 링크 제외)
                        additional_selectors = [
                            "a[href*='/zf_user/company']:not([href*='/zf_user/jobs'])",
                            ".item_recruit a[href*='/zf_user/company']",
                            ".list_item a[href*='/zf_user/company']",
                            "[class*='item'] a[href*='/zf_user/company']",
                        ]
                        for selector in additional_selectors:
                            try:
                                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                                print(f"[사람인] 방법5 ({selector}): {len(elements)}개 요소 발견")
                                for elem in elements:
                                    href = elem.get_attribute("href")
                                    if href and "/zf_user/company" in href:
                                        if href.startswith("/"):
                                            href = "https://www.saramin.co.kr" + href
                                        href_clean = href.split("?")[0].split("#")[0].rstrip("/")
                                        # 정확히 /zf_user/company/로 시작하는 링크만 수집
                                        if (href_clean.startswith("https://www.saramin.co.kr/zf_user/company/") or 
                                            href_clean.startswith("/zf_user/company/")):
                                            if ("/zf_user/company-info" not in href_clean and 
                                                "/zf_user/company-review" not in href_clean and
                                                "/zf_user/jobs" not in href_clean and
                                                "/zf_user/company/" in href_clean and
                                                href_clean not in links):
                                                links.append(href_clean)
                                                print(f"[사람인] 링크 추가 (추가선택자 {selector}): {href_clean}")
                                            else:
                                                print(f"[사람인] 링크 제외 (잘못된 경로): {href_clean}")
                                        else:
                                            print(f"[사람인] 링크 제외 (형식 불일치): {href_clean}")
                            except Exception as e:
                                print(f"[사람인] 방법5 선택자 오류 ({selector}): {e}")
                                pass
                except Exception as e:
                    print(f"[사람인] 방법5 오류: {e}")
                    pass
                
                # 방법 6: 사람인 검색 결과에서 회사명 클릭 영역 찾기
                try:
                    # 각 채용 공고 항목에서 회사명 영역 찾기
                    recruit_items = driver.find_elements(By.CSS_SELECTOR, ".item_recruit, .list_item, [class*='item_recruit'], [class*='list_item']")
                    print(f"[사람인] 방법6: {len(recruit_items)}개 채용 공고 항목 발견")
                    for item in recruit_items:
                        try:
                            # 회사명 영역에서 회사 상세 페이지 링크 찾기
                            # 회사명은 보통 h2, h3, strong, a 태그에 있음
                            company_name_elements = item.find_elements(By.CSS_SELECTOR, "h2 a, h3 a, strong a, .company_name a, a.company_name")
                            for elem in company_name_elements:
                                href = elem.get_attribute("href")
                                if href and "/zf_user/company" in href:
                                    if href.startswith("/"):
                                        href = "https://www.saramin.co.kr" + href
                                    href_clean = href.split("?")[0].split("#")[0].rstrip("/")
                                    # 정확히 /zf_user/company/로 시작하는 링크만 수집
                                    if (href_clean.startswith("https://www.saramin.co.kr/zf_user/company/") or 
                                        href_clean.startswith("/zf_user/company/")):
                                        if ("/zf_user/company-info" not in href_clean and 
                                            "/zf_user/company-review" not in href_clean and
                                            "/zf_user/jobs" not in href_clean and
                                            "/zf_user/company/" in href_clean and
                                            href_clean not in links):
                                            links.append(href_clean)
                                            print(f"[사람인] 링크 추가 (회사명 영역): {href_clean}")
                                        else:
                                            print(f"[사람인] 링크 제외 (잘못된 경로): {href_clean}")
                                    else:
                                        print(f"[사람인] 링크 제외 (형식 불일치): {href_clean}")
                        except Exception as e:
                            print(f"[사람인] 방법6 항목 처리 오류: {e}")
                            pass
                except Exception as e:
                    print(f"[사람인] 방법6 오류: {e}")
                    pass
            except Exception as e:
                print(f"[사람인] 링크 추출 오류: {e}")
            
            print(f"[사람인] 페이지 {current_page}에서 {len(links) - page_links_count}개 링크 발견 (총 {len(links)}개)")
            
            # 목표 개수에 도달했거나, 기본 페이지 범위를 넘었는데 새 링크가 없으면 중단
            if max_urls > 0 and len(links) >= max_urls:
                break
            
            # 기본 페이지 범위를 넘었는데 새 링크가 없으면 중단
            if current_page > pages and len(links) == page_links_count:
                break
            
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)
            except:
                pass
            
            current_page += 1
        
    except Exception as e:
        print(f"[사람인] 크롤링 오류: {e}")
    
    print(f"[사람인] 총 {len(links)}개 회사 링크 수집 완료")
    return list(set(links))


def get_jobkorea_company_links(driver, keyword, pages=10, max_urls=0):
    """잡코리아 사이트에서 회사 검색하여 회사 상세 페이지 링크 수집 (목표 개수에 도달할 때까지 페이지 확장)"""
    links = []
    current_page = 1
    max_pages = pages * 3  # 최대 3배까지 확장 가능
    
    try:
        while current_page <= max_pages:
            url = f"https://www.jobkorea.co.kr/Search/?stext={quote_plus(keyword)}&tabType=recruit&Page_No={current_page}"
            driver.get(url)
            time.sleep(2)
            
            page_links_count = len(links)
            
            # 잡코리아 검색 결과에서 회사 상세 페이지 링크 찾기
            selectors = [
                "a[href*='/company']",      # 회사 상세 페이지 링크
                "a.company_name",
                ".company_name a",
                "div.company_name a",
                "a[href*='company_view']",
                "a[href*='Company']",
            ]
            
            for selector in selectors:
                try:
                    results = driver.find_elements(By.CSS_SELECTOR, selector)
                    for res in results:
                        href = res.get_attribute("href")
                        if href and ("jobkorea.co.kr" in href or href.startswith("/")):
                            # 상대 경로를 절대 경로로 변환
                            if href.startswith("/"):
                                href = "https://www.jobkorea.co.kr" + href
                            if "company" in href.lower() and href not in links:
                                links.append(href)
                except:
                    pass
            
            # 목표 개수에 도달했으면 중단
            if max_urls > 0 and len(links) >= max_urls:
                break
            
            # 기본 페이지 범위를 넘었는데 새 링크가 없으면 중단
            if current_page > pages and len(links) == page_links_count:
                break
            
            # 다음 페이지 체크
            try:
                next_button = driver.find_element(By.CSS_SELECTOR, ".paging a.next, .paging .next")
                if "disabled" in next_button.get_attribute("class") or not next_button.is_enabled():
                    if current_page > pages:  # 기본 페이지 범위를 넘었으면 중단
                        break
            except:
                if current_page > pages:  # 기본 페이지 범위를 넘었으면 중단
                    break
            
            current_page += 1
    except Exception as e:
        pass
    
    return list(set(links))


def get_albamon_company_links(driver, keyword, pages=10, max_urls=0):
    """알바몬 사이트에서 회사 검색하여 회사 상세 페이지 링크 수집 (목표 개수에 도달할 때까지 페이지 확장)"""
    links = []
    current_page = 1
    max_pages = pages * 3  # 최대 3배까지 확장 가능
    
    try:
        while current_page <= max_pages:
            url = f"https://www.albamon.com/list/gi/mon_list.asp?keyword={quote_plus(keyword)}&page={current_page}"
            driver.get(url)
            time.sleep(2)
            
            page_links_count = len(links)
            
            # 알바몬 검색 결과에서 회사 상세 페이지 링크 찾기
            selectors = [
                "a[href*='company']",       # 회사 상세 페이지 링크
                "a.company_name",
                ".company_name a",
                "div.company_name a",
                "a[href*='gi_view']",
            ]
            
            for selector in selectors:
                try:
                    results = driver.find_elements(By.CSS_SELECTOR, selector)
                    for res in results:
                        href = res.get_attribute("href")
                        if href and ("albamon.com" in href or href.startswith("/")):
                            # 상대 경로를 절대 경로로 변환
                            if href.startswith("/"):
                                href = "https://www.albamon.com" + href
                            if ("company" in href.lower() or "gi_view" in href.lower()) and href not in links:
                                links.append(href)
                except:
                    pass
            
            # 목표 개수에 도달했으면 중단
            if max_urls > 0 and len(links) >= max_urls:
                break
            
            # 기본 페이지 범위를 넘었는데 새 링크가 없으면 중단
            if current_page > pages and len(links) == page_links_count:
                break
            
            # 다음 페이지 체크
            try:
                next_button = driver.find_element(By.CSS_SELECTOR, ".paging a.next, .paging .next")
                if "disabled" in next_button.get_attribute("class") or not next_button.is_enabled():
                    if current_page > pages:  # 기본 페이지 범위를 넘었으면 중단
                        break
            except:
                if current_page > pages:  # 기본 페이지 범위를 넘었으면 중단
                    break
            
            current_page += 1
    except Exception as e:
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
        
        # 채용 사이트별 특별 처리
        url_lower = url.lower()
        
        # 사람인 (saramin.co.kr) - 회사 상세 페이지 또는 company-info/view 페이지인 경우
        if "saramin.co.kr" in url_lower and ("/zf_user/company" in url_lower or "/zf_user/company-info/view" in url_lower):
            print(f"[사람인 상세페이지] 접근 중: {url}")
            try:
                # /zf_user/company-info/view 페이지인 경우, 회사 상세 페이지로 이동하는 링크 찾기
                if "/zf_user/company-info/view" in url_lower:
                    print(f"[사람인] company-info/view 페이지에서 회사 상세 페이지 링크 찾기...")
                    time.sleep(2)
                    
                    # 회사 상세 페이지로 가는 링크 찾기
                    company_detail_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/zf_user/company/']")
                    for link in company_detail_links:
                        try:
                            href = link.get_attribute("href")
                            if href and "/zf_user/company/" in href and "/zf_user/company-info" not in href:
                                if href.startswith("/"):
                                    href = "https://www.saramin.co.kr" + href
                                print(f"[사람인] 회사 상세 페이지로 이동: {href}")
                                driver.get(href)
                                time.sleep(2)
                                break
                        except:
                            pass
                
                # 페이지가 완전히 로드될 때까지 대기
                time.sleep(2)
                print(f"[사람인 상세페이지] 페이지 제목: {driver.title}")
                
                # 회사명 추출 (사람인 구조) - 더 많은 선택자 시도
                company_selectors = [
                    "h1.company_name", 
                    "div.company_name", 
                    ".company_name",
                    "h2.company_name", 
                    "span.company_name", 
                    "strong.company_name",
                    ".company_info h1", 
                    ".company_info .company_name",
                    ".company_header h1",
                    ".company_header .company_name",
                    "h1[class*='company']",
                    ".item_company h1",
                    ".item_company .company_name",
                    "div[class*='company_name']",
                    # 페이지 제목에서 회사명 추출 시도
                ]
                for selector in company_selectors:
                    try:
                        elem = driver.find_element(By.CSS_SELECTOR, selector)
                        company_text = elem.text.strip()
                        # 이상한 텍스트 필터링 (너무 짧거나 일반적인 문구 제외)
                        if company_text and len(company_text) > 2 and not any(x in company_text for x in ['에 대해', '많은 사람', '궁금해', '검색', '사람인']):
                            info["회사명"] = company_text
                            print(f"[사람인] 회사명 추출 성공 (선택자: {selector}): {company_text}")
                            break
                    except:
                        pass
                
                # 선택자로 못 찾았으면 페이지 제목에서 추출 시도
                if not info["회사명"]:
                    try:
                        page_title = driver.title.strip()
                        # 제목에서 회사명 추출 (예: "회사명 | 사람인" 형식)
                        if "|" in page_title:
                            company_name = page_title.split("|")[0].strip()
                            if company_name and len(company_name) > 2:
                                info["회사명"] = company_name
                                print(f"[사람인] 회사명 추출 성공 (제목): {company_name}")
                    except:
                        pass
                
                # 홈페이지 URL 추출 (기업정보 섹션에서)
                homepage_url = None
                print(f"[사람인 상세페이지] 홈페이지 URL 추출 시작...")
                
                # 방법 1: dt/dd 구조에서 찾기 (가장 정확)
                try:
                    # "홈페이지" 텍스트가 있는 dt 요소 찾기
                    dt_elements = driver.find_elements(By.XPATH, "//dt[contains(text(), '홈페이지')]")
                    print(f"[사람인 상세페이지] 방법1: dt 요소 {len(dt_elements)}개 발견")
                    for dt in dt_elements:
                        try:
                            # 다음 형제 dd 요소 찾기
                            dd = dt.find_element(By.XPATH, "./following-sibling::dd[1]")
                            # dd 안의 링크 찾기
                            link = dd.find_element(By.CSS_SELECTOR, "a[href^='http']")
                            homepage_url = link.get_attribute("href")
                            if homepage_url:
                                print(f"[사람인 상세페이지] 방법1 성공: {homepage_url}")
                                break
                        except:
                            try:
                                # dd 안에 직접 텍스트로 URL이 있을 수도 있음
                                dd = dt.find_element(By.XPATH, "./following-sibling::dd[1]")
                                dd_text = dd.text.strip()
                                if dd_text.startswith("http"):
                                    homepage_url = dd_text.split()[0]  # 첫 번째 단어가 URL일 가능성
                                    print(f"[사람인 상세페이지] 방법1 성공 (텍스트): {homepage_url}")
                                    break
                            except:
                                pass
                except Exception as e:
                    print(f"[사람인 상세페이지] 방법1 오류: {e}")
                    pass
                
                # 방법 2: 페이지 텍스트에서 정규식으로 찾기
                if not homepage_url:
                    try:
                        body_text = driver.find_element(By.TAG_NAME, "body").text
                        homepage_patterns = [
                            r'홈페이지\s*[:\s]\s*(https?://[^\s\n\r]+)',
                            r'홈페이지\s*[:\s]\s*(www\.[^\s\n\r]+)',
                            r'홈페이지[^\n]*?(https?://[^\s\n\r]+)',
                        ]
                        for pattern in homepage_patterns:
                            match = re.search(pattern, body_text)
                            if match:
                                homepage_url = match.group(1).strip()
                                # URL이 잘린 경우 처리
                                if homepage_url.endswith('...'):
                                    homepage_url = homepage_url[:-3]
                                if not homepage_url.startswith("http"):
                                    homepage_url = "http://" + homepage_url
                                break
                    except:
                        pass
                
                # 방법 3: 모든 외부 링크를 검사하여 홈페이지 찾기
                if not homepage_url:
                    try:
                        all_links = driver.find_elements(By.CSS_SELECTOR, "a[href^='http']:not([href*='saramin.co.kr'])")
                        for link in all_links:
                            href = link.get_attribute("href")
                            if href and not any(x in href.lower() for x in ['saramin', 'jobkorea', 'albamon', 'facebook', 'twitter', 'instagram', 'linkedin', 'youtube']):
                                # 링크의 부모 요소나 형제 요소에서 "홈페이지" 텍스트 확인
                                try:
                                    # 링크의 부모 요소들 확인
                                    parent = link.find_element(By.XPATH, "./ancestor::*[contains(text(), '홈페이지')]")
                                    if parent:
                                        homepage_url = href
                                        break
                                except:
                                    try:
                                        # 링크의 이전 형제 요소 확인
                                        prev_sibling = link.find_element(By.XPATH, "./preceding-sibling::*[contains(text(), '홈페이지')]")
                                        if prev_sibling:
                                            homepage_url = href
                                            break
                                    except:
                                        pass
                    except:
                        pass
                
                # 방법 4: 기업정보 섹션에서 직접 찾기
                if not homepage_url:
                    try:
                        company_info_section = driver.find_elements(By.CSS_SELECTOR, ".company_info, .company-detail, .company_detail, .info_list, dl.info_list, .company-detail-info")
                        for section in company_info_section:
                            try:
                                links = section.find_elements(By.CSS_SELECTOR, "a[href^='http']:not([href*='saramin'])")
                                for link in links:
                                    href = link.get_attribute("href")
                                    if href and not any(x in href.lower() for x in ['saramin', 'jobkorea', 'albamon', 'facebook', 'twitter', 'instagram', 'linkedin', 'youtube']):
                                        # 링크 주변 텍스트 확인
                                        try:
                                            link_text = link.text.strip()
                                            parent = link.find_element(By.XPATH, "./..")
                                            parent_text = parent.text
                                            # "홈페이지" 텍스트가 있거나, 링크가 외부 도메인인 경우
                                            if "홈페이지" in parent_text or "홈페이지" in link_text or (href.startswith("http") and "." in href.split("//")[1].split("/")[0]):
                                                homepage_url = href
                                                break
                                        except:
                                            # 링크만으로 판단 (외부 도메인)
                                            if href.startswith("http") and "." in href.split("//")[1].split("/")[0]:
                                                homepage_url = href
                                                break
                                if homepage_url:
                                    break
                            except:
                                pass
                    except:
                        pass
                
                # 방법 5: 페이지에서 "홈페이지" 텍스트 옆의 모든 외부 링크 확인
                if not homepage_url:
                    try:
                        # "홈페이지" 텍스트가 있는 모든 요소 찾기
                        homepage_labels = driver.find_elements(By.XPATH, "//*[contains(text(), '홈페이지')]")
                        for label in homepage_labels:
                            try:
                                # 부모 요소에서 링크 찾기
                                parent = label.find_element(By.XPATH, "./ancestor::*[contains(@class, 'info') or contains(@class, 'detail') or contains(@class, 'company')][1]")
                                links = parent.find_elements(By.CSS_SELECTOR, "a[href^='http']:not([href*='saramin'])")
                                for link in links:
                                    href = link.get_attribute("href")
                                    if href and not any(x in href.lower() for x in ['saramin', 'jobkorea', 'albamon', 'facebook', 'twitter', 'instagram']):
                                        homepage_url = href
                                        break
                                if homepage_url:
                                    break
                            except:
                                pass
                    except:
                        pass
                
                # 홈페이지가 있으면 홈페이지로 이동해서 footer에서 이메일 추출
                if homepage_url:
                    print(f"[사람인 상세페이지] 홈페이지 URL 발견: {homepage_url}")
                    try:
                        print(f"[사람인 상세페이지] 홈페이지로 이동 중: {homepage_url}")
                        driver.get(homepage_url)
                        time.sleep(2)
                        print(f"[사람인 상세페이지] 홈페이지 접근 완료: {driver.title}")
                        
                        # 페이지 하단으로 스크롤하여 footer 로드
                        try:
                            # 페이지 끝까지 스크롤
                            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                            time.sleep(1)
                            # 추가로 스크롤 (일부 사이트는 동적 로딩)
                            driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
                            time.sleep(1)
                            # footer 요소가 보이도록 스크롤
                            try:
                                footer_elem = driver.find_element(By.CSS_SELECTOR, "footer, #footer, .footer")
                                driver.execute_script("arguments[0].scrollIntoView(true);", footer_elem)
                                time.sleep(1)
                            except:
                                pass
                        except:
                            pass
                        
                        # footer에서 이메일 추출 (더 정확하게)
                        print(f"[사람인 상세페이지] footer에서 이메일 추출 시작...")
                        footer_selectors = [
                            "footer",
                            "#footer",
                            ".footer",
                            "div[class*='footer']",
                            "div[id*='footer']",
                            "div[class*='Footer']",
                            "div[id*='Footer']",
                            ".site-footer",
                            "#site-footer",
                            "footer *",
                            ".footer *",
                            "#footer *",
                        ]
                        
                        footer_elements = []
                        footer_htmls = []
                        
                        # 모든 footer 요소 찾기
                        for selector in footer_selectors:
                            try:
                                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                                for elem in elements:
                                    try:
                                        # footer 요소 자체와 그 하위 요소들 모두 수집
                                        if elem.tag_name.lower() in ['footer', 'div', 'section']:
                                            footer_elements.append(elem)
                                    except:
                                        pass
                            except:
                                pass
                        
                        print(f"[사람인 상세페이지] footer 요소 {len(footer_elements)}개 발견")
                        
                        # 중복 제거 (같은 요소는 한 번만)
                        seen_elements = set()
                        unique_footer_elements = []
                        for elem in footer_elements:
                            try:
                                elem_id = elem.id
                                if elem_id not in seen_elements:
                                    seen_elements.add(elem_id)
                                    unique_footer_elements.append(elem)
                            except:
                                unique_footer_elements.append(elem)
                        
                        # footer 요소들의 HTML과 텍스트 수집
                        all_footer_text = ""
                        all_footer_html = ""
                        for footer_elem in unique_footer_elements:
                            try:
                                text = footer_elem.text
                                html = footer_elem.get_attribute("innerHTML") or footer_elem.get_attribute("outerHTML") or ""
                                if text:
                                    all_footer_text += "\n" + text
                                if html:
                                    all_footer_html += "\n" + html
                            except:
                                pass
                        
                        # footer HTML에서 이메일 추출 (정규식으로)
                        if all_footer_html or all_footer_text:
                            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                            found_emails = []
                            
                            # HTML에서 먼저 찾기 (더 정확함)
                            if all_footer_html:
                                found_emails.extend(re.findall(email_pattern, all_footer_html))
                            
                            # 텍스트에서도 찾기
                            if all_footer_text:
                                found_emails.extend(re.findall(email_pattern, all_footer_text))
                            
                            # 불필요한 이메일 제외
                            real_emails = []
                            for email in found_emails:
                                email_lower = email.lower()
                                # 이미지 파일명 제외
                                if email_lower.endswith(('.png', '.jpg', '.gif', '.svg', '.jpeg', '.webp', '.ico', '.css', '.js')):
                                    continue
                                # noreply 등 제외
                                if any(x in email_lower for x in ['noreply', 'no-reply', 'donotreply', 'example.com', 'test.com', 'sample.com', 'placeholder']):
                                    continue
                                # 시스템 이메일은 포함 (admin@, webmaster@ 등)
                                real_emails.append(email)
                            
                            if real_emails:
                                # 중복 제거하고 최대 3개
                                unique_emails = list(set(real_emails))[:3]
                                info["이메일"] = ", ".join(unique_emails)
                                print(f"[사람인 상세페이지] 이메일 발견: {info['이메일']}")
                            else:
                                print(f"[사람인 상세페이지] footer에서 이메일을 찾지 못함")
                        
                        # footer에서 mailto 링크 찾기 (더 정확하게)
                        if not info["이메일"]:
                            mailto_selectors = [
                                "footer a[href^='mailto:']",
                                "#footer a[href^='mailto:']",
                                ".footer a[href^='mailto:']",
                                "footer a[href*='mailto']",
                                "#footer a[href*='mailto']",
                                ".footer a[href*='mailto']",
                            ]
                            for selector in mailto_selectors:
                                try:
                                    mailto_links = driver.find_elements(By.CSS_SELECTOR, selector)
                                    for link in mailto_links:
                                        try:
                                            href = link.get_attribute("href")
                                            if href and "mailto:" in href:
                                                email = href.split("mailto:")[1].split("?")[0].split("&")[0].strip()
                                                if email and "@" in email and "." in email:
                                                    info["이메일"] = email
                                                    break
                                        except:
                                            pass
                                    if info["이메일"]:
                                        break
                                except:
                                    pass
                        
                        # footer에서 "E-Mail", "이메일", "Email" 등의 텍스트 옆에 있는 이메일 찾기
                        if not info["이메일"]:
                            try:
                                # "E-Mail", "이메일", "Email" 등의 텍스트가 있는 요소 찾기
                                email_label_patterns = [
                                    "//*[contains(text(), 'E-Mail')]",
                                    "//*[contains(text(), '이메일')]",
                                    "//*[contains(text(), 'Email')]",
                                    "//*[contains(text(), 'e-mail')]",
                                    "//*[contains(text(), 'E-mail')]",
                                ]
                                
                                for pattern in email_label_patterns:
                                    try:
                                        labels = driver.find_elements(By.XPATH, pattern)
                                        for label in labels:
                                            try:
                                                # 형제 요소나 부모 요소에서 이메일 찾기
                                                parent = label.find_element(By.XPATH, "./..")
                                                parent_text = parent.text
                                                parent_html = parent.get_attribute("innerHTML") or ""
                                                
                                                email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                                                emails = re.findall(email_pattern, parent_text + " " + parent_html)
                                                
                                                if emails:
                                                    email = emails[0]
                                                    email_lower = email.lower()
                                                    # noreply, example, test만 제외 (시스템 이메일은 포함)
                                                    if not any(x in email_lower for x in ['noreply', 'no-reply', 'example', 'test']):
                                                        info["이메일"] = email
                                                        break
                                            except:
                                                pass
                                        if info["이메일"]:
                                            break
                                    except:
                                        pass
                            except:
                                pass
                        
                        # footer에서 이메일을 못 찾았으면 전체 페이지에서 찾기 (더 강화)
                        if not info["이메일"]:
                            try:
                                # 전체 페이지 HTML과 텍스트 모두 검사
                                body_elem = driver.find_element(By.TAG_NAME, "body")
                                body_text = body_elem.text
                                body_html = body_elem.get_attribute("innerHTML") or body_elem.get_attribute("outerHTML") or ""
                                
                                email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                                found_emails = []
                                
                                # HTML에서 먼저 찾기 (더 정확함)
                                if body_html:
                                    found_emails.extend(re.findall(email_pattern, body_html))
                                
                                # 텍스트에서도 찾기
                                if body_text:
                                    found_emails.extend(re.findall(email_pattern, body_text))
                                
                                # 불필요한 이메일 제외
                                real_emails = []
                                for email in found_emails:
                                    email_lower = email.lower()
                                    # 이미지 파일명 제외
                                    if email_lower.endswith(('.png', '.jpg', '.gif', '.svg', '.jpeg', '.webp', '.ico', '.css', '.js')):
                                        continue
                                    # noreply 등 제외
                                    if any(x in email_lower for x in ['noreply', 'no-reply', 'donotreply', 'example.com', 'test.com', 'sample.com', 'placeholder']):
                                        continue
                                    real_emails.append(email)
                                
                                if real_emails:
                                    # 중복 제거하고 최대 3개
                                    unique_emails = list(set(real_emails))[:3]
                                    info["이메일"] = ", ".join(unique_emails)
                            except:
                                pass
                        
                        # 여전히 이메일을 못 찾았으면 전체 페이지에서 찾기 (더 강화)
                        if not info["이메일"]:
                            try:
                                print(f"[사람인 상세페이지] 전체 페이지에서 이메일 추출 시도...")
                                # 페이지 전체 HTML과 텍스트 모두 검사
                                body_elem = driver.find_element(By.TAG_NAME, "body")
                                body_text = body_elem.text
                                body_html = body_elem.get_attribute("innerHTML") or body_elem.get_attribute("outerHTML") or ""
                                
                                email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                                found_emails = []
                                
                                # HTML에서 먼저 찾기 (더 정확함)
                                if body_html:
                                    found_emails.extend(re.findall(email_pattern, body_html))
                                
                                # 텍스트에서도 찾기
                                if body_text:
                                    found_emails.extend(re.findall(email_pattern, body_text))
                                
                                # 불필요한 이메일 제외
                                real_emails = []
                                for email in found_emails:
                                    email_lower = email.lower()
                                    # 이미지 파일명 제외
                                    if email_lower.endswith(('.png', '.jpg', '.gif', '.svg', '.jpeg', '.webp', '.ico', '.css', '.js')):
                                        continue
                                    # noreply 등 제외
                                    if any(x in email_lower for x in ['noreply', 'no-reply', 'donotreply', 'example.com', 'test.com', 'sample.com', 'placeholder']):
                                        continue
                                    real_emails.append(email)
                                
                                if real_emails:
                                    # 중복 제거하고 최대 3개
                                    unique_emails = list(set(real_emails))[:3]
                                    info["이메일"] = ", ".join(unique_emails)
                                    print(f"[사람인 상세페이지] 전체 페이지에서 이메일 발견: {info['이메일']}")
                            except Exception as e:
                                print(f"[사람인 상세페이지] 전체 페이지 이메일 추출 오류: {e}")
                                pass
                        
                        # URL을 홈페이지로 업데이트
                        info["URL"] = homepage_url
                        
                        # 홈페이지에서 회사 정보 추출 강화
                        print(f"[사람인 상세페이지] 홈페이지에서 회사 정보 추출 시작...")
                        try:
                            body_elem = driver.find_element(By.TAG_NAME, "body")
                            body_text = body_elem.text
                            body_html = body_elem.get_attribute("innerHTML") or ""
                            
                            # 회사명 추출 (홈페이지에서)
                            if not info["회사명"]:
                                company_selectors_homepage = [
                                    "h1", "h2", ".company_name", ".corp_name", 
                                    ".site-title", ".logo", "[class*='company']",
                                    "[class*='corp']", ".brand", ".site-name"
                                ]
                                for selector in company_selectors_homepage:
                                    try:
                                        elems = driver.find_elements(By.CSS_SELECTOR, selector)
                                        for elem in elems:
                                            text = elem.text.strip()
                                            if text and len(text) > 2 and len(text) < 50:
                                                # 이상한 텍스트 필터링
                                                if not any(x in text for x in ['에 대해', '많은 사람', '궁금해', '검색', '사람인', 'HOME', 'MENU']):
                                                    info["회사명"] = text
                                                    print(f"[사람인 상세페이지] 회사명 추출 (홈페이지): {text}")
                                                    break
                                        if info["회사명"]:
                                            break
                                    except:
                                        pass
                                
                                # 정규식으로 회사명 추출
                                if not info["회사명"]:
                                    company_patterns = [
                                        r'(?:회사명|상호|법인명|업체명|기업명)\s*[:\s]\s*([^\n\r,|(]{2,30})',
                                        r'\(주\)\s*([가-힣a-zA-Z0-9\s]{2,20})',
                                        r'([가-힣]{2,15}(?:주식회사|㈜|\(주\)))',
                                        r'((?:주식회사|㈜)\s*[가-힣a-zA-Z0-9]{2,15})',
                                    ]
                                    for pattern in company_patterns:
                                        match = re.search(pattern, body_text)
                                        if match:
                                            company_name = match.group(1).strip()
                                            if len(company_name) > 2:
                                                info["회사명"] = company_name
                                                print(f"[사람인 상세페이지] 회사명 추출 (정규식): {company_name}")
                                                break
                            
                            # 대표자명 추출 (홈페이지에서)
                            if not info["대표자명"]:
                                ceo_patterns = [
                                    r'(?:대표자?|대표이사|CEO|대표자명)\s*[:\s]\s*([가-힣]{2,5})',
                                    r'대표이사\s*([가-힣]{2,5})',
                                    r'CEO\s*[:\s]\s*([가-힣]{2,5})',
                                ]
                                for pattern in ceo_patterns:
                                    match = re.search(pattern, body_text, re.IGNORECASE)
                                    if match:
                                        ceo_name = match.group(1).strip()
                                        if len(ceo_name) >= 2:
                                            info["대표자명"] = ceo_name
                                            print(f"[사람인 상세페이지] 대표자명 추출: {ceo_name}")
                                            break
                            
                            # 회사 주소 추출 (홈페이지에서)
                            if not info["회사주소"]:
                                address_patterns = [
                                    r'(?:주소|소재지|사업장\s*소재지|본사)\s*[:\s]\s*([^\n\r]{10,80})',
                                    r'((?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)[^\n\r]{10,70})',
                                ]
                                for pattern in address_patterns:
                                    match = re.search(pattern, body_text)
                                    if match:
                                        addr = match.group(1).strip()[:80]
                                        info["회사주소"] = addr
                                        print(f"[사람인 상세페이지] 회사주소 추출: {addr}")
                                        break
                        except Exception as e:
                            print(f"[사람인 상세페이지] 회사 정보 추출 오류: {e}")
                            pass
                    except Exception as e:
                        print(f"[사람인 상세페이지] 홈페이지 처리 오류: {e}")
                        import traceback
                        print(traceback.format_exc())
                        pass
                else:
                    # 홈페이지를 못 찾았으면 사람인 페이지에서 직접 이메일 추출 시도
                    print(f"[사람인 상세페이지] 홈페이지 URL을 찾지 못함 - 사람인 페이지에서 직접 이메일 추출 시도")
                    try:
                        email_elements = driver.find_elements(By.CSS_SELECTOR, "a[href^='mailto:'], .email, .contact_email")
                        print(f"[사람인 상세페이지] 사람인 페이지에서 이메일 요소 {len(email_elements)}개 발견")
                        for elem in email_elements:
                            email_text = elem.get_attribute("href") or elem.text
                            if "@" in email_text:
                                email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', email_text)
                                if email_match:
                                    info["이메일"] = email_match.group(0)
                                    print(f"[사람인 상세페이지] 사람인 페이지에서 이메일 발견: {info['이메일']}")
                                    break
                    except Exception as e:
                        print(f"[사람인 상세페이지] 사람인 페이지에서 이메일 추출 오류: {e}")
                        pass
            except:
                pass
        
        # 잡코리아 (jobkorea.co.kr)
        elif "jobkorea.co.kr" in url_lower:
            try:
                # 회사명 추출
                company_selectors = [
                    "h1.company_name", ".company_name", "div.company_info h2",
                    ".company_title", "h2.company_title"
                ]
                for selector in company_selectors:
                    try:
                        elem = driver.find_element(By.CSS_SELECTOR, selector)
                        if elem.text.strip():
                            info["회사명"] = elem.text.strip()
                            break
                    except:
                        pass
                
                # 이메일 추출
                email_elements = driver.find_elements(By.CSS_SELECTOR, "a[href^='mailto:'], .email, .contact")
                for elem in email_elements:
                    email_text = elem.get_attribute("href") or elem.text
                    if "@" in email_text:
                        email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', email_text)
                        if email_match:
                            info["이메일"] = email_match.group(0)
                            break
            except:
                pass
        
        # 알바몬 (albamon.com)
        elif "albamon.com" in url_lower:
            try:
                # 회사명 추출
                company_selectors = [
                    "h1.company_name", ".company_name", ".company_title",
                    "div.company_info h2", "h2.company_name"
                ]
                for selector in company_selectors:
                    try:
                        elem = driver.find_element(By.CSS_SELECTOR, selector)
                        if elem.text.strip():
                            info["회사명"] = elem.text.strip()
                            break
                    except:
                        pass
                
                # 이메일 추출
                email_elements = driver.find_elements(By.CSS_SELECTOR, "a[href^='mailto:'], .email, .contact_email")
                for elem in email_elements:
                    email_text = elem.get_attribute("href") or elem.text
                    if "@" in email_text:
                        email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', email_text)
                        if email_match:
                            info["이메일"] = email_match.group(0)
                            break
            except:
                pass
        
        # 일반 이메일 추출 (채용 사이트에서 이메일을 못 찾은 경우 또는 일반 사이트)
        if not info["이메일"]:
            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            found_emails = re.findall(email_pattern, body_text)
            real_emails = [e for e in found_emails if not e.lower().endswith(('.png', '.jpg', '.gif', '.svg', '.jpeg', '.webp'))]
            # 채용 사이트 관련 이메일 제외 (noreply, no-reply 등)
            real_emails = [e for e in real_emails if not any(x in e.lower() for x in ['noreply', 'no-reply', 'donotreply', 'jobkorea', 'saramin', 'albamon'])]
            if real_emails:
                info["이메일"] = ", ".join(set(real_emails[:3]))  # 최대 3개만
        
        # 회사명 추출 (채용 사이트에서 못 찾은 경우 또는 일반 사이트)
        if not info["회사명"]:
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


def run_crawling(keywords, session_id, max_count=0, search_pages=10):
    global user_sessions
    
    user_sessions[session_id] = {
        "results": [],
        "status": {"running": True, "progress": "크롤링 시작...", "completed": False},
        "stop_flag": False
    }
    
    try:
        driver = setup_driver()
    except Exception as e:
        user_sessions[session_id]["status"]["progress"] = f"드라이버 초기화 실패: {str(e)[:100]}"
        user_sessions[session_id]["status"]["running"] = False
        user_sessions[session_id]["status"]["completed"] = True
        return
    
    try:
        # URL 수집 (네이버 + 다음 + 사람인 + 잡코리아 + 알바몬)
        # URL은 충분히 많이 수집해야 함 (이메일이 없는 회사도 많으므로)
        # 목표 개수는 회사 정보 수집 단계에서만 체크
        all_urls = []
        for i, keyword in enumerate(keywords):
            # 정지 버튼 체크
            if user_sessions[session_id]["stop_flag"]:
                break
                
            if keyword.strip():
                # 네이버 검색 (파워링크 포함)
                user_sessions[session_id]["status"]["progress"] = f"'{keyword}' 네이버 검색 중... ({i+1}/{len(keywords)}) [파워링크 포함]"
                naver_urls = get_naver_links(driver, keyword.strip(), pages=search_pages, max_urls=0)
                all_urls.extend(naver_urls)
                user_sessions[session_id]["status"]["progress"] = f"'{keyword}' 네이버 검색 완료: {len(naver_urls)}개 링크 발견"
                
                # 정지 버튼 체크
                if user_sessions[session_id]["stop_flag"]:
                    break
                
                # 사람인 검색 (테스트용 1페이지만)
                user_sessions[session_id]["status"]["progress"] = f"'{keyword}' 사람인 검색 중... ({i+1}/{len(keywords)}) [1페이지]"
                saramin_urls = get_saramin_company_links(driver, keyword.strip(), pages=1, max_urls=0)
                all_urls.extend(saramin_urls)
                user_sessions[session_id]["status"]["progress"] = f"'{keyword}' 사람인 검색 완료: {len(saramin_urls)}개 링크 발견"
                # 
                # # 정지 버튼 체크
                # if user_sessions[session_id]["stop_flag"]:
                #     break
                # 
                # # 다음 검색 (주석처리 - 디버깅용)
                # user_sessions[session_id]["status"]["progress"] = f"'{keyword}' 다음 검색 중... ({i+1}/{len(keywords)})"
                # daum_urls = get_daum_links(driver, keyword.strip(), pages=search_pages, max_urls=0)
                # all_urls.extend(daum_urls)
                # 
                # # 정지 버튼 체크
                # if user_sessions[session_id]["stop_flag"]:
                #     break
                # 
                # # 잡코리아 검색 (주석처리 - 디버깅용)
                # user_sessions[session_id]["status"]["progress"] = f"'{keyword}' 잡코리아 검색 중... ({i+1}/{len(keywords)})"
                # jobkorea_urls = get_jobkorea_company_links(driver, keyword.strip(), pages=search_pages, max_urls=0)
                # all_urls.extend(jobkorea_urls)
                # 
                # # 정지 버튼 체크
                # if user_sessions[session_id]["stop_flag"]:
                #     break
                # 
                # # 알바몬 검색 (주석처리 - 디버깅용)
                # user_sessions[session_id]["status"]["progress"] = f"'{keyword}' 알바몬 검색 중... ({i+1}/{len(keywords)})"
                # albamon_urls = get_albamon_company_links(driver, keyword.strip(), pages=search_pages, max_urls=0)
                # all_urls.extend(albamon_urls)
        
        target_urls = list(set(all_urls))
        total_sites = len(target_urls)
        print(f"[디버깅] 총 수집된 URL: {len(all_urls)}개, 중복 제거 후: {total_sites}개")
        
        # 링크가 없으면 에러 메시지 출력하고 종료
        if total_sites == 0:
            error_msg = "회사 상세 페이지 링크를 찾지 못했습니다. 사람인 검색 결과 페이지 구조가 변경되었을 수 있습니다."
            print(f"[오류] {error_msg}")
            user_sessions[session_id]["status"]["progress"] = error_msg
            user_sessions[session_id]["status"]["completed"] = True
            user_sessions[session_id]["status"]["running"] = False
            try:
                driver.quit()
            except:
                pass
            return
        
        # 수집된 링크 목록 출력
        print(f"[디버깅] 수집된 링크 목록 (총 {len(target_urls)}개):")
        for idx, url in enumerate(target_urls[:20]):  # 최대 20개까지 출력
            print(f"  {idx+1}. {url}")
        if len(target_urls) > 20:
            print(f"  ... 외 {len(target_urls) - 20}개")
        
        user_sessions[session_id]["status"]["progress"] = f"총 {total_sites}개 사이트 발견. 정보 수집 중... (목표: {max_count if max_count > 0 else '무제한'}개)"
        
        # 중복 체크용 세트
        seen_urls = set()
        seen_companies = set()
        seen_emails = set()
        
        duplicate_count = 0
        
        # 회사 정보 수집 - 목표 개수에 도달할 때까지 계속
        for i, url in enumerate(target_urls):
            print(f"[디버깅] ({i+1}/{total_sites}) 상세 페이지 접근 시도: {url}")
            # 정지 버튼 체크
            if user_sessions[session_id]["stop_flag"]:
                user_sessions[session_id]["status"]["progress"] = f"정지됨! {len(user_sessions[session_id]['results'])}개 회사 정보 수집"
                break
            
            target_text = f"/{max_count}" if max_count > 0 else ""
            current_count = len(user_sessions[session_id]["results"])
            user_sessions[session_id]["status"]["progress"] = f"정보 수집 중... ({i+1}/{total_sites}) - 수집: {current_count}{target_text}개"
            
            print(f"[디버깅] ({i+1}/{total_sites}) 처리 중: {url}")
            try:
                info = extract_company_info(driver, url)
                print(f"[디버깅] 추출된 정보 - 회사명: '{info['회사명']}', 이메일: '{info['이메일']}'")
            except Exception as e:
                print(f"[오류] 상세 페이지 처리 중 오류 발생: {e}")
                import traceback
                print(traceback.format_exc())
                # 오류가 발생해도 기본 정보는 저장
                info = {
                    "URL": url,
                    "사이트명": "",
                    "회사명": "",
                    "대표자명": "",
                    "회사주소": "",
                    "이메일": ""
                }
                info["URL"] = url
            
            # 이메일이 없어도 모든 사이트를 결과에 추가 (확인용)
            # 중복 체크: 이메일 중복만 체크 (URL과 회사명은 완전히 제거 - 모든 방문 사이트 표시)
            url_base = url.split('?')[0].rstrip('/')  # 쿼리 파라미터 제거
            company_name = info["회사명"].strip() if info["회사명"] else ""
            email_key = info["이메일"].lower().strip() if info["이메일"] else ""
            
            is_duplicate = False
            duplicate_reason = ""
            
            # 이메일이 있는 경우에만 이메일 중복 체크 (가장 중요)
            # 같은 이메일이면 중복으로 처리
            if email_key and email_key in seen_emails:
                is_duplicate = True
                duplicate_reason = "이메일 중복"
            
            # URL 중복 체크 완전히 제거 - 모든 방문한 사이트를 표시
            # 회사명 중복 체크도 완전히 제거 - 모든 방문한 사이트를 표시
            
            if not is_duplicate:
                seen_urls.add(url_base)
                if company_name:
                    seen_companies.add(company_name)
                if email_key:
                    seen_emails.add(email_key)
                
                # 이메일이 없어도 모든 사이트 추가
                user_sessions[session_id]["results"].append(info)
                print(f"[디버깅] 추가됨 (총 {len(user_sessions[session_id]['results'])}개) - URL: {url_base[:80]}, 회사명: {company_name[:30]}, 이메일: {email_key[:30]}")
            else:
                duplicate_count += 1
                print(f"[디버깅] 중복 제외됨: {duplicate_reason} (총 중복: {duplicate_count}개) - URL: {url_base[:80]}")
            
            # 상태 업데이트 (중복 여부와 관계없이 항상 실행)
            if not info["이메일"]:
                if "saramin.co.kr" in url.lower() and "/zf_user/company" in url.lower():
                    if info["URL"] == url:
                        # 홈페이지를 못 찾은 경우
                        user_sessions[session_id]["status"]["progress"] = f"정보 수집 중... ({i+1}/{total_sites}) - 수집: {len(user_sessions[session_id]['results'])}{target_text}개 [홈페이지 미발견]"
                    else:
                        # 홈페이지로 이동했지만 이메일을 못 찾은 경우
                        user_sessions[session_id]["status"]["progress"] = f"정보 수집 중... ({i+1}/{total_sites}) - 수집: {len(user_sessions[session_id]['results'])}{target_text}개 [이메일 미발견]"
                else:
                    user_sessions[session_id]["status"]["progress"] = f"정보 수집 중... ({i+1}/{total_sites}) - 수집: {len(user_sessions[session_id]['results'])}{target_text}개"
            
            # 목표 개수 체크는 이메일이 있는 경우만 (중복 여부와 관계없이 항상 체크)
            if max_count > 0:
                # 이메일이 있는 항목만 카운트
                email_count = sum(1 for r in user_sessions[session_id]["results"] if r.get("이메일") and r.get("이메일") != "-")
                if email_count >= max_count:
                    user_sessions[session_id]["status"]["progress"] = f"완료! 이메일 {max_count}개 도달 (목표 달성, 총 {len(user_sessions[session_id]['results'])}개 사이트 수집)"
                    print(f"[디버깅] 목표 개수 도달! 이메일 {email_count}개 >= 목표 {max_count}개")
                    break
        
        if not user_sessions[session_id]["stop_flag"]:
            collected_count = len(user_sessions[session_id]['results'])
            email_collected_count = sum(1 for r in user_sessions[session_id]["results"] if r.get("이메일"))
            user_sessions[session_id]["status"]["progress"] = f"완료! 총 {collected_count}개 사이트 정보 수집 (이메일 {email_collected_count}개, 중복 제외 {duplicate_count}개)"
            print(f"[디버깅] 최종 결과: 총 {collected_count}개 수집, 이메일 {email_collected_count}개, 중복 제외 {duplicate_count}개")
        
        user_sessions[session_id]["status"]["completed"] = True
        
    except Exception as e:
        # 에러 발생 시 상태 업데이트
        error_msg = str(e)
        user_sessions[session_id]["status"]["progress"] = f"오류 발생: {error_msg[:100]}"
        user_sessions[session_id]["status"]["completed"] = True
        import traceback
        print(f"크롤링 오류: {traceback.format_exc()}")
    finally:
        try:
            driver.quit()
        except:
            pass
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
    
    try:
        # 세션 ID 확인
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())
        session_id = session['session_id']
        
        # 이 사용자가 이미 크롤링 중인지 확인
        if session_id in user_sessions and user_sessions[session_id]["status"]["running"]:
            return jsonify({"error": "이미 크롤링이 진행 중입니다."}), 400
        
        # JSON 데이터 파싱
        if not request.is_json:
            return jsonify({"error": "JSON 형식의 데이터가 필요합니다."}), 400
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "요청 데이터가 없습니다."}), 400
        
        keywords = data.get('keywords', [])
        max_count = data.get('maxCount', 0)  # 0이면 제한 없음
        search_pages = data.get('searchPages', 10)  # 기본 10페이지로 고정
        
        # keywords가 리스트가 아닌 경우 처리
        if not isinstance(keywords, list):
            keywords = [keywords] if keywords else []
        
        if not keywords or all(k.strip() == '' for k in keywords):
            return jsonify({"error": "검색어를 입력해주세요."}), 400
        
        # 백그라운드에서 크롤링 실행
        thread = threading.Thread(target=run_crawling, args=(keywords, session_id, max_count, search_pages))
        thread.start()
        
        return jsonify({"message": "크롤링을 시작합니다."})
    except Exception as e:
        import traceback
        error_msg = str(e)
        print(f"Crawl endpoint error: {traceback.format_exc()}")
        return jsonify({"error": f"크롤링 시작 중 오류가 발생했습니다: {error_msg}"}), 500
    
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
    try:
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
    except Exception as e:
        return jsonify({
            "running": False,
            "progress": f"오류: {str(e)[:50]}",
            "completed": False,
            "count": 0
        }), 500


@app.route('/results')
def results():
    try:
        session_id = session.get('session_id')
        
        if not session_id or session_id not in user_sessions:
            return jsonify([])
        
        return jsonify(user_sessions[session_id]["results"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


