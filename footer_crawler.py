import time
import re
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from urllib.parse import quote_plus


# 제외할 사이트 목록 (회사 웹사이트와 관련 없는 사이트들)
EXCLUDE_DOMAINS = [
    # 네이버 관련
    "naver.com", "naver.me",
    
    # 뉴스/미디어
    "news", "snmnews", "chosun", "joongang", "hani", "donga", "hankyung",
    "mk.co.kr", "mt.co.kr", "edaily", "newsis", "yonhap", "yna.co.kr",
    
    # 블로그/커뮤니티
    "tistory", "blog", "brunch", "medium.com", "velog", "notion.so",
    "cafe.daum", "dcinside", "clien", "ruliweb", "fmkorea",
    
    # 채용/구인구직
    "saramin", "jobkorea", "job.gg.go.kr", "incruit", "wanted", "jobaba",
    "alba", "work.go.kr", "catch.co.kr", "superookie",
    
    # 쇼핑몰/오픈마켓
    "gmarket", "11st", "coupang", "auction", "interpark", "wemakeprice",
    "tmon", "ssg.com", "lotte", "shinsegae", "hmall", "gsshop",
    "alibaba", "aliexpress", "amazon", "ebay", "taobao",
    
    # 해외 사이트 전체 제외
    "mfgrobots", "alibaba", "made-in-china", "globalsources",
    "firstmold", "djmolding", "sanonchina", "yujebearing",
    "custom-plastic-molds", "rjcmold", "formlabs",
    "juliertech", ".cn", ".com.cn",
    
    # 지도/위키/기타
    "wikipedia", "namu.wiki", "openstreetmap", "google.com", "youtube",
    "facebook", "instagram", "twitter", "linkedin",
    
    # 광고
    "ad.search", "searchad", "adsense",
]


def is_valid_company_url(url):
    """회사 웹사이트로 적합한 URL인지 확인"""
    url_lower = url.lower()
    for domain in EXCLUDE_DOMAINS:
        if domain in url_lower:
            return False
    return True


def setup_driver():
    """브라우저 드라이버 설정 (창 안 뜨는 Headless 모드)"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # 브라우저 창 없이 백그라운드 실행
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    # 봇 탐지 회피용 User-Agent
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver


def get_naver_links(driver, keyword, pages=5):
    """네이버 웹 검색 탭에서 URL 수집 (where=web)"""
    links = []
    print(f"[검색] '{keyword}' 네이버 웹 검색 시작...")
    
    for page in range(1, pages + 1):
        # 네이버 웹 검색 URL (where=web으로 웹사이트만 검색)
        url = f"https://search.naver.com/search.naver?where=web&query={quote_plus(keyword)}&page={page}"
        driver.get(url)
        time.sleep(2)  # 로딩 대기
        
        print(f"   [페이지 {page}] 검색 중...")

        # 네이버 웹 검색 결과 링크 선택자들
        selectors = [
            "a.link_tit",              # 웹사이트 제목 링크
            "div.total_tit a",         # 웹 검색 제목
            "a.total_tit",             # 웹 검색 제목 (다른 형식)
            "div.api_txt_lines a",     # 웹 검색 결과 링크
            "a[href*='http']:not([href*='naver'])",  # 외부 링크
        ]
        
        page_links = []
        for selector in selectors:
            try:
                results = driver.find_elements(By.CSS_SELECTOR, selector)
                for res in results:
                    href = res.get_attribute("href")
                    if href and href.startswith("http"):
                        # 유효한 회사 URL만 추가
                        if is_valid_company_url(href):
                            page_links.append(href)
            except:
                pass
        
        links.extend(page_links)
        print(f"   [페이지 {page}] {len(page_links)}개 유효 링크 발견 (총 {len(links)}개)")
    
    # 중복 제거
    unique_links = list(set(links))
    print(f"[결과] 중복 제거 후 {len(unique_links)}개 회사 사이트")
    return unique_links


def extract_company_info(driver, url):
    """사이트 접속 후 회사 정보 추출 (회사명, 사이트명, 이메일, 대표자명, 주소)"""
    info = {
        "URL": url,
        "사이트명": "",
        "회사명": "",
        "대표자명": "",
        "회사주소": "",
        "이메일": ""
    }
    
    try:
        print(f"   ㄴ 접속 중: {url}")
        driver.set_page_load_timeout(15)
        driver.get(url)
        time.sleep(2)
        
        # 1. 사이트명 (페이지 타이틀)
        info["사이트명"] = driver.title.strip() if driver.title else ""
        
        # 2. 페이지 전체 텍스트 가져오기
        body_text = driver.find_element(By.TAG_NAME, "body").text
        
        # 3. 이메일 추출
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        found_emails = re.findall(email_pattern, body_text)
        real_emails = [e for e in found_emails if not e.lower().endswith(('.png', '.jpg', '.gif', '.svg', '.jpeg', '.webp'))]
        info["이메일"] = ", ".join(set(real_emails))
        
        # 4. 회사명 추출 (다양한 패턴)
        company_patterns = [
            r'(?:회사명|상호|법인명|업체명|기업명)\s*[:\s]\s*([^\n\r,|(]{2,30})',
            r'(?:상\s*호)\s*[:\s]\s*([^\n\r,|(]{2,30})',
            r'\(주\)\s*([가-힣a-zA-Z0-9\s]{2,20})',
            r'([가-힣]{2,15}(?:주식회사|㈜|\(주\)))',
            r'((?:주식회사|㈜)\s*[가-힣a-zA-Z0-9]{2,15})',
        ]
        for pattern in company_patterns:
            match = re.search(pattern, body_text)
            if match:
                info["회사명"] = match.group(1).strip()
                break
        
        # 5. 대표자명 추출
        ceo_patterns = [
            r'(?:대표자?|대표이사|CEO|대표자명)\s*[:\s]\s*([가-힣]{2,5})',
            r'(?:대\s*표)\s*[:\s]\s*([가-힣]{2,5})',
            r'대표이사\s*([가-힣]{2,5})',
        ]
        for pattern in ceo_patterns:
            match = re.search(pattern, body_text, re.IGNORECASE)
            if match:
                info["대표자명"] = match.group(1).strip()
                break
        
        # 6. 회사 주소 추출
        address_patterns = [
            r'(?:주소|소재지|사업장\s*소재지|본사)\s*[:\s]\s*([^\n\r]{10,80})',
            r'(?:주\s*소)\s*[:\s]\s*([^\n\r]{10,80})',
            r'((?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)[^\n\r]{10,70})',
        ]
        for pattern in address_patterns:
            match = re.search(pattern, body_text)
            if match:
                addr = match.group(1).strip()
                # 주소가 너무 길면 자르기
                if len(addr) > 80:
                    addr = addr[:80] + "..."
                info["회사주소"] = addr
                break
        
        return info

    except Exception as e:
        print(f"      [오류] 에러 발생: {e}")
        return info


def main():
    # 여러 키워드로 검색 (비슷한 키워드 3~4개)
    keywords = [
        "금형 제조업체",
        "사출금형 업체",
        "프레스금형 업체",
        "금형 공장",
    ]
    
    driver = setup_driver()
    
    try:
        # 1. 모든 키워드로 URL 수집
        all_urls = []
        for keyword in keywords:
            urls = get_naver_links(driver, keyword, pages=5)  # 키워드당 5페이지
            all_urls.extend(urls)
        
        # 중복 제거
        target_urls = list(set(all_urls))
        print(f"\n[결과] 총 {len(target_urls)}개의 고유 사이트를 발견했습니다.")
        
        results = []
        
        # 2. 각 사이트 순회하며 회사 정보 수집
        for url in target_urls:
            info = extract_company_info(driver, url)
            if info["이메일"]:  # 이메일이 있는 경우만 저장
                print(f"      [발견] 회사: {info['회사명'] or '미확인'} | 대표: {info['대표자명'] or '미확인'} | 이메일: {info['이메일']}")
                results.append(info)
            else:
                print("      [없음] 이메일 없음")
        
        # 3. 엑셀 저장
        if results:
            df = pd.DataFrame(results)
            # 컬럼 순서 지정
            columns = ["회사명", "사이트명", "이메일", "대표자명", "회사주소", "URL"]
            df = df[columns]
            file_name = "금형업체_회사정보_리스트.xlsx"
            df.to_excel(file_name, index=False)
            print(f"\n[완료] '{file_name}' 파일로 저장되었습니다.")
            print(f"[통계] 총 {len(results)}개 회사 정보 수집")
        else:
            print("\n[실패] 수집된 이메일이 없습니다.")
            
    finally:
        driver.quit()


if __name__ == "__main__":
    main()

