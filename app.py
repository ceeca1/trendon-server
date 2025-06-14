from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from bs4 import BeautifulSoup
import requests as req
import time
import json
import pandas as pd
import openai
import difflib
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ✅ OpenAI API Key
openai.api_key = os.getenv("OPENAI_API_KEY")

# ✅ 데이터 불러오기
stock_db = pd.read_csv("naver_theme_stocks_full.csv")
code_df = pd.read_csv("data.csv", encoding="euc-kr")
code_df["단축코드"] = code_df["단축코드"].astype(str).str.zfill(6)

with open("keyword.json", encoding="utf-8") as f:
    keyword_rules = json.load(f)

# ✅ 뉴스 크롤링
@app.route('/news')
def get_news():
    keyword = request.args.get('q', '속보')
    url = f"https://search.daum.net/search?w=news&q={keyword}"
    headers = {"User-Agent": "Mozilla/5.0"}
    res = req.get(url, headers=headers)
    soup = BeautifulSoup(res.text, 'html.parser')

    news_list = []
    items = soup.select('div.c-item-content')
    for item in items[:10]:
        title_tag = item.select_one('div.item-title a')
        img_tag = item.select_one('div.item-thumb img')
        if title_tag:
            news_list.append({
                'title': title_tag.get_text(strip=True),
                'link': title_tag['href'],
                'image': img_tag['src'] if img_tag and img_tag.has_attr('src') else ""
            })

    return Response(
        response=json.dumps(news_list, ensure_ascii=False, indent=2),
        content_type='application/json; charset=utf-8'
    )

# ✅ 뉴스 본문 추출
def crawl_news_body(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = req.get(url, headers=headers)
    soup = BeautifulSoup(res.text, "html.parser")
    article = soup.find("section") or soup.find("div", {"class": "article_view"})
    return article.get_text(strip=True) if article else ""

# ✅ GPT 요약 및 키워드 추출
def ask_gpt_for_summary_and_keywords(text):
    prompt = f"""
    다음은 뉴스 본문입니다. 이 뉴스를 다음 조건에 맞게 요약 및 분석해줘:

    1. 뉴스 내용을 한 문장으로 간결하게 요약해줘.
    2. 이 뉴스에서 가장 중요한 키워드를 3개 뽑아줘. 단, 키워드는 주식 시장에서 관련 종목 또는 테마를 연상시킬 수 있는 단어로 골라줘.
    3. 출력 형식은 아래와 같이 해줘:

    요약: [여기에 한 줄 요약]
    키워드: [키워드1, 키워드2, 키워드3]

    뉴스: {text}
    """
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()

# ✅ GPT 응답 파싱
def extract_summary_and_keywords(response_text):
    summary, keywords = "", []
    lines = response_text.split("\n")
    for line in lines:
        if line.startswith("요약:"):
            summary = line.replace("요약:", "").strip()
        elif line.startswith("키워드:"):
            keywords = line.replace("키워드:", "").strip(" []").split(",")
            keywords = [kw.strip() for kw in keywords]
    return summary, keywords

# ✅ 종목명 → 단축코드
def get_code_by_name(name):
    row = code_df[code_df['한글 종목약명'] == name]
    return str(row['단축코드'].values[0]).zfill(6) if not row.empty else None

# ✅ 실시간 주가 및 등락률 크롤링 + 링크 포함
def get_stock_price_and_rate(name):
    code = get_code_by_name(name)
    if not code:
        return {"name": name, "price": None, "rate": None, "volume": None, "link": None}

    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    res = req.get(url, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(res.text, 'html.parser')
    table = soup.select_one("table.type2")
    prices, volumes = [], []

    for tr in table.select("tr")[1:]:
        cols = tr.find_all("td")
        if len(cols) >= 7 and cols[0].text.strip():
            try:
                price = int(cols[1].text.replace(',', ''))
                volume = int(cols[6].text.replace(',', ''))
                prices.append(price)
                volumes.append(volume)
            except:
                continue
        if len(prices) >= 2:
            break

    if len(prices) < 2 or not volumes:
        return {"name": name, "price": None, "rate": None, "volume": None, "link": None}

    today, yesterday = prices[0], prices[1]
    rate = round((today - yesterday) / yesterday * 100, 2)
    return {
        "name": name,
        "price": today,
        "rate": f"{rate:+.2f}%",
        "volume": volumes[0],
        "link": f"https://finance.naver.com/item/main.naver?code={code}"
    }

# ✅ 키워드 → 종목 추천
def recommend_stocks(keywords):
    matched_stocks = set()
    all_themes = stock_db["테마명"].dropna().unique().tolist()

    for kw in keywords:
        matched_themes = [theme for theme, rule_keywords in keyword_rules.items() if any(rk in kw for rk in rule_keywords)]

        if matched_themes:
            for theme in matched_themes:
                matched_stocks.update(stock_db[stock_db["테마명"] == theme]["종목명"].tolist())
        else:
            closest = difflib.get_close_matches(kw, all_themes, n=1, cutoff=0.4)
            if closest:
                matched_stocks.update(stock_db[stock_db["테마명"] == closest[0]]["종목명"].tolist())

    return list(matched_stocks)

# ✅ /analyze → 뉴스 분석
@app.route('/analyze', methods=['POST'])
def analyze_news():
    url = request.form.get("url")
    if not url:
        return jsonify({"error": "URL is required"}), 400

    news_text = crawl_news_body(url)
    if not news_text:
        return jsonify({"error": "Failed to fetch news content"}), 500

    gpt_response = ask_gpt_for_summary_and_keywords(news_text)
    summary, keywords = extract_summary_and_keywords(gpt_response)
    matched_names = recommend_stocks(keywords)

    stock_list = []
    for name in matched_names:
        info = get_stock_price_and_rate(name)
        if info["price"] is None:
            continue
        transaction_amount = info["price"] * info["volume"]
        stock_list.append({
            "name": info["name"],
            "price": info["price"],
            "rate": info["rate"],
            "volume": info["volume"],
            "transaction_amount": transaction_amount,
            "link": info["link"]
        })

    stock_list = sorted(stock_list, key=lambda x: x["transaction_amount"], reverse=True)[:5]

    return jsonify({
        "summary": summary,
        "keywords": keywords,
        "recommended_stocks": stock_list
    })

# ✅ 서버 실행
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
