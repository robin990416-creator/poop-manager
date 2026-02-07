import streamlit as st
import google.generativeai as genai
import PIL.Image
import json
import datetime
import time
import pandas as pd
import os

# ---------------------------------------------------------
# [설정] API 키 & 데이터 파일
# ---------------------------------------------------------
# 1. API 키 보안 확인
if "GOOGLE_API_KEY" in st.secrets:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
else:
    st.error("🚨 API 키가 없습니다. Secrets 설정을 확인해주세요.")
    st.stop()

# 2. 파일 설정 (CSV 사용!)
DATA_FILE = "user_health_data.json"
FOOD_DB_FILE = "food_db.csv"

genai.configure(api_key=GOOGLE_API_KEY, transport='rest')
model = genai.GenerativeModel('gemini-flash-latest')

# ---------------------------------------------------------
# 🕵️‍♂️ [비밀 공식] 배변량 계산 (비율을 Secrets에서 가져옴)
# ---------------------------------------------------------
def calculate_poop_amount(protein, fat, carbs, fiber):
    try:
        p_r = st.secrets["P_RATIO"]
        f_r = st.secrets["F_RATIO"]
        c_r = st.secrets["C_RATIO"]
        fib_r = st.secrets["FIBER_RATIO"]
        w_f = st.secrets["WATER_FACTOR"]
        b_f = st.secrets["BAC_FACTOR"]
    except:
        # 비상용 기본값
        p_r, f_r, c_r, fib_r, w_f, b_f = 0.1, 0.1, 0.2, 0.9, 2.33, 1.3

    solid_waste = (protein * p_r) + (fat * f_r) + (carbs * c_r) + (fiber * fib_r)
    total_poop = (solid_waste * w_f) * b_f
    
    return round(total_poop, 1)

# ---------------------------------------------------------
# 데이터 관리 함수
# ---------------------------------------------------------
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"users": {}}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_food_db():
    if os.path.exists(FOOD_DB_FILE):
        try:
            # CSV 읽기 (인코딩 에러 나면 engine='python' 추가)
            df = pd.read_csv(FOOD_DB_FILE)
            return df.set_index('menu').to_dict(orient='index')
        except Exception as e:
            st.error(f"CSV 파일 읽기 실패: {e}")
            return {}
    return {}

# ---------------------------------------------------------
# AI 분석 함수
# ---------------------------------------------------------
def analyze_food_image(image):
    image.thumbnail((512, 512)) 
    prompt = """
    이 음식 사진을 분석해서 JSON 형식으로만 답해줘.
    1. 음식 이름 (food_name): 메뉴명 (예: 김치찌개)
    2. 총 중량 (total_weight_g): 사진의 음식 전체 무게(g)
    {
        "food_name": "음식 이름",
        "total_weight_g": 숫자,
        "comment": "짧은 평가"
    }
    """
    try:
        response = model.generate_content([prompt, image])
        text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except:
        return None

# ---------------------------------------------------------
# [UI 구성]
# ---------------------------------------------------------
st.set_page_config(page_title="장 건강 매니저", page_icon="💩")

if 'user_name' not in st.session_state:
    st.title("💩 영훈이의 시크릿 배변 일기장")
    name_input = st.text_input("이름을 입력해주세요")
    if st.button("시작하기"):
        if name_input:
            st.session_state['user_name'] = name_input
            st.rerun()
    st.stop()

user_name = st.session_state['user_name']
data = load_data()
food_db = load_food_db()

if user_name not in data["users"]:
    data["users"][user_name] = {
        "last_poop": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "meals_log": [],
        "current_poop_stock": 0.0
    }
user_data = data["users"][user_name]

st.title(f"🚽 {user_name}님의 장 건강 매니저")
st.metric(label="현재 뱃속 예상 배변량", value=f"{user_data['current_poop_stock']:.1f}g")

tab1, tab2 = st.tabs(["🍽️ 식사 기록", "💩 배변 기록"])

# --- 탭 1: 식사 기록 ---
with tab1:
    uploaded_file = st.file_uploader("식사 사진 업로드", type=['png', 'jpg', 'jpeg'])
    if uploaded_file:
        image = PIL.Image.open(uploaded_file)
        st.image(image, width=300)
        
        # 👇 [수정됨] 시간 선택 기능 추가
        st.write("🕒 **언제 드셨나요?**")
        col_d, col_t = st.columns(2)
        input_date = col_d.date_input("날짜", datetime.datetime.now())
        input_time = col_t.time_input("시간", datetime.datetime.now())
        
        if st.button("AI 분석 시작 🚀"):
            with st.spinner('분석 중...'):
                result = analyze_food_image(image)
                if result:
                    st.session_state['analysis_result'] = result
        
        if 'analysis_result' in st.session_state:
            res = st.session_state['analysis_result']
            name = res['food_name']
            weight = res['total_weight_g']
            
            st.success(f"🔍 메뉴: {name} ({weight}g)")
            
            # DB 매칭
            if name in food_db:
                nut = food_db[name]
                st.info("📚 데이터베이스(CSV) 정보를 사용합니다!")
            else:
                st.warning("데이터베이스에 없는 메뉴입니다. (기본값 적용)")
                nut = {"protein": 5, "fat": 5, "carbs": 20, "fiber": 2}

            ratio = st.slider("먹은 양 비율", 0.5, 2.0, 1.0, 0.1)
            real_w = weight * ratio
            
            # 영양소 계산
            p = nut['protein'] * (real_w / 100)
            f = nut['fat'] * (real_w / 100)
            c = nut['carbs'] * (real_w / 100)
            fib = nut['fiber'] * (real_w / 100)
            
            # 배변량 계산
            poop = calculate_poop_amount(p, f, c, fib)
            
            st.write(f"### 💩 예상 배변량: +{poop}g")
            
            if st.button("저장하기"):
                # 선택한 날짜와 시간을 합쳐서 저장
                eat_datetime = datetime.datetime.combine(input_date, input_time)
                
                log = {
                    "date": eat_datetime.strftime("%Y-%m-%d %H:%M"), # 👈 선택한 시간으로 저장
                    "food": name,
                    "poop": poop
                }
                user_data['meals_log'].append(log)
                user_data['current_poop_stock'] += poop
                save_data(data)
                del st.session_state['analysis_result']
                st.rerun()

# --- 탭 2: 배변 기록 ---
with tab2:
    if st.button("쾌변 완료 (비우기) 🚽", type="primary"):
        user_data['current_poop_stock'] = 0.0
        user_data['last_poop'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M") # 비운 시간은 현재 시간
        save_data(data)
        st.balloons()
        st.rerun()
        
    if user_data['meals_log']:
        # 최신순으로 보여주기 (뒤집기)
        display_data = user_data['meals_log'][::-1]
        st.dataframe(pd.DataFrame(display_data))
