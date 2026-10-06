import os
from flask import Flask, request, jsonify
from anthropic import Anthropic

app = Flask(__name__)
client = Anthropic()

def calculate_saju(birth_date, name=""):
    """Claude API를 사용한 사주팔자 분석"""
    prompt = f"""
사주팔자 분석 (카카오톡 봇용)

생년월일: {birth_date}
{f'이름: {name}' if name else ''}

다음 항목들을 분석해주세요:
1. 오행 분석 (천간지, 오행 구성)
2. 성격 특성 (강점, 약점)
3. 운세 (전반적 운명 경향)
4. 직업 운 (추천 직업)
5. 재정 운
6. 인간관계
7. 개운 방법 (색상, 방향, 숫자)

포맷:
【오행 분석】
...

【성격 특성】
...

(간결하고 포맷팅된 답변)
"""
    
    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        return f"오류: {str(e)}"

@app.route('/saju', methods=['POST'])
def saju():
    """카카오톡 OpenBuilder 스킬 처리"""
    try:
        data = request.json
        birth_date = data.get('birth_date', '')
        name = data.get('name', '')
        
        if not birth_date:
            return jsonify({"message": "생년월일을 입력해주세요 (YYYY-MM-DD)"})
        
        result = calculate_saju(birth_date, name)
        
        return jsonify({
            "message": f"🌟 사주팔자 분석 결과\n\n생년월일: {birth_date}\n\n{result}"
        })
    except Exception as e:
        return jsonify({"message": f"오류 발생: {str(e)}"}), 500

@app.route('/health', methods=['GET'])
def health():
    """헬스 체크"""
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
