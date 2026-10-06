from flask import Flask, request, jsonify
import os

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

@app.route('/saju', methods=['POST'])
def saju():
<<<<<<< HEAD
    data = request.json
    birth_date = data.get('birth_date', '')
    return jsonify({"message": f"사주 분석 준비 중: {birth_date}"})
=======
    return jsonify({"message": "사주팔자 분석 준비 중입니다!"})
>>>>>>> ee9285aabf571bd8486b339188de12ee31fbdf23

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
