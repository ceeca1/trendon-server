import requests

# Flask 서버 주소
url = "http://localhost:5000/analyze"

# 분석할 뉴스 URL (예시: Daum 뉴스 링크)
payload = {
    "url": "http://v.daum.net/v/20250609000405975"
}

try:
    response = requests.post(url, json=payload)

    print("Status Code:", response.status_code)
    print("----- 결과 출력 -----")
    if response.status_code == 200:
        data = response.json()
        print("✅ 요약:", data["summary"])
        print("📌 키워드:", data["keywords"])
        print("📈 추천 종목:", data["stocks"])
    else:
        print(response.text)

except Exception as e:
    print("요청 중 오류 발생:", e)
