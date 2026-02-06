# 👇 맥북 네트워크 멈춤 해결
import os
os.environ["GRPC_DNS_RESOLVER"] = "native"
import socket

import streamlit as st
import google.generativeai as genai
import PIL.Image
import json
import datetime
import time
import pandas as pd

# ---------------------------------------------------------
# [설정] API 키 & 통신 방식
# ---------------------------------------------------------
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# ⚠️ 본인의 API 키 확인! (깃허브 올릴 땐 st.secrets 사용 추천)
if "GOOGLE_API_KEY" in st.secrets:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
else:
    st.error("🚨 API 키가 없습니다. 설정(Secrets)에 키를 넣어주세요!")
    st.stop()
DATA_FILE = "user_health_data.json"

genai.configure(api_key=GOOGLE_API_KEY, transport='rest')
model = genai.GenerativeModel('gemini-flash-latest')

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

def analyze_food_image(image):
    image.thumbnail((512, 512)) 
    
    prompt = """
    이 음식 사진을 분석해서 JSON 형식으로만 답해줘. 사진 전체에 있는 음식의 총량을 추정해.
    {
        "food_name": "음식 이름",
        "weight_g": 숫자(g단위),
        "calories": 숫자(kcal),
        "comment": "짧은 평가"
    }
    """
    try:
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        response = model.generate_content([prompt, image], safety_settings=safety_settings)
        text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        st.error(f"🚨 분석 중 오류 발생: {str(e)}")
        return None

def predict_next_poop(last_poop_str, current_stomach_volume):
    last_poop_time = datetime.datetime.strptime(last_poop_str, "%Y-%m-%d %H:%M")
    base_transit_time = 24 
    
    if current_stomach_volume > 1000:
        transit_hours = base_transit_time - 6
        reason = "폭식 (가속 +6시간)"
    elif current_stomach_volume > 600:
        transit_hours = base_transit_time - 3
        reason = "충분한 식사 (가속 +3시간)"
    elif current_stomach_volume < 200:
        transit_hours = base_transit_time + 4
        reason = "적은 식사량 (지연 -4시간)"
    else:
        transit_hours = base_transit_time
        reason = "일반적인 소화 속도"

    next_poop_time = last_poop_time + datetime.timedelta(hours=transit_hours)
    return next_poop_time, transit_hours, reason

# ---------------------------------------------------------
# UI 구성
# ---------------------------------------------------------
st.set_page_config(page_title="장 건강 매니저", page_icon="🚽")

# [1] 로그인
if 'user_name' not in st.session_state:
    st.title("👋 환영합니다!")
    name_input = st.text_input("이름을 입력해주세요", placeholder="예: 영훈")
    if st.button("시작하기"):
        if name_input:
            st.session_state['user_name'] = name_input
            st.rerun()
    st.stop()

# [2] 메인 앱
user_name = st.session_state['user_name']
data = load_data()

if user_name not in data["users"]:
    data["users"][user_name] = {
        "last_poop": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "meals_since_last_poop": [],
        "total_weight_in_stomach": 0
    }

user_data = data["users"][user_name]

col1, col2 = st.columns([3, 1])
with col1:
    st.title(f"🚽 {user_name}님의 장 건강 매니저")
with col2:
    if st.button("로그아웃"):
        del st.session_state['user_name']
        st.rerun()

st.info(f"현재 뱃속에는 **{user_data['total_weight_in_stomach']}g**의 음식물이 들어있어요.")

tab1, tab2, tab3 = st.tabs(["🍽️ 식사 기록", "💩 배변/시간수정", "📊 상세 리포트"])

# --- 탭 1: 식사 기록 ---
with tab1:
    st.subheader("음식 사진 분석")
    uploaded_file = st.file_uploader("사진 찍기 또는 업로드", type=['png', 'jpg', 'jpeg'])

    if uploaded_file:
        image = PIL.Image.open(uploaded_file)
        st.image(image, caption='선택한 사진', use_container_width=True)
        
        # [추가됨] 식사 인원 입력
        col_type, col_people = st.columns(2)
        with col_type:
            meal_type = st.radio("식사 종류", ["아침", "점심", "저녁", "야식/간식"])
        with col_people:
            num_people = st.number_input("함께 먹은 인원 (나 포함)", min_value=1, value=1, step=1)

        if st.button("AI 분석 시작 🚀", type="primary"):
            with st.spinner('AI가 전체 양을 분석 중입니다...'):
                result = analyze_food_image(image)
                if result:
                    st.session_state['current_analysis'] = result
        
        if 'current_analysis' in st.session_state:
            result = st.session_state['current_analysis']
            
            # 1인분 계산
            my_weight = int(result['weight_g'] / num_people)
            my_calories = int(result['calories'] / num_people)

            with st.container(border=True):
                st.subheader(result['food_name'])
                st.caption(f"💡 전체 {result['weight_g']}g / {num_people}명이서 식사")
                
                # 결과 표시 (내 몫 강조)
                c1, c2, c3 = st.columns(3)
                c1.metric("내 섭취량", f"{my_weight}g", delta=f"전체 {result['weight_g']}g")
                c2.metric("내 칼로리", f"{my_calories}kcal")
                c3.write(f"**종류:** {meal_type}")
                st.write(f"👉 {result['comment']}")

            if st.button("내 몫만 기록 저장하기"):
                meal_record = {
                    "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "type": meal_type,
                    "name": f"{result['food_name']} (1/{num_people}인분)",
                    "weight": my_weight,
                    "calories": my_calories,
                    "people_count": num_people # 나중에 참고용으로 저장
                }
                user_data['meals_since_last_poop'].append(meal_record)
                user_data['total_weight_in_stomach'] += my_weight # 내 뱃속엔 내 몫만 추가
                save_data(data)
                
                del st.session_state['current_analysis']
                st.toast(f"내 몫({my_weight}g)만 저장되었습니다! 💾")
                time.sleep(1)
                st.rerun()

# --- 탭 2: 배변 기록 & 수정 ---
with tab2:
    st.subheader("배변 활동 관리")
    
    st.write("#### 1. 지금 막 화장실을 다녀오셨나요?")
    if st.button("지금 쾌변했습니다! (뱃속 비우기) 🚽", type="primary"):
        user_data['last_poop'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        user_data['meals_since_last_poop'] = []
        user_data['total_weight_in_stomach'] = 0
        save_data(data)
        st.balloons()
        st.success("상쾌하시겠어요! 기록이 초기화되었습니다.")
        st.rerun()

    st.divider()

    st.write("#### 2. 배변 시간을 직접 수정하고 싶으신가요?")
    current_last_poop = datetime.datetime.strptime(user_data['last_poop'], "%Y-%m-%d %H:%M")
    
    col_d, col_t = st.columns(2)
    new_date = col_d.date_input("날짜 선택", current_last_poop.date())
    new_time = col_t.time_input("시간 선택", current_last_poop.time())

    if st.button("이 시간으로 수정하기 🛠️"):
        new_datetime = datetime.datetime.combine(new_date, new_time)
        user_data['last_poop'] = new_datetime.strftime("%Y-%m-%d %H:%M")
        save_data(data)
        st.success(f"수정 완료! ({new_datetime.strftime('%m/%d %H:%M')})")
        time.sleep(1)
        st.rerun()

# --- 탭 3: 상세 리포트 ---
with tab3:
    st.subheader("📊 상세 리포트")
    
    meals = user_data['meals_since_last_poop']
    if meals:
        df = pd.DataFrame(meals)
        # 테이블 컬럼 정리
        if 'people_count' in df.columns:
            df['비고'] = df['people_count'].apply(lambda x: f"{x}인 식사" if x > 1 else "혼밥")
        
        display_df = df[['date', 'type', 'name', 'weight', 'calories']]
        if '비고' in df.columns:
            display_df['비고'] = df['비고']
            
        display_df.columns = ['시간', '구분', '메뉴', '내 섭취량(g)', '칼로리', '비고']
        st.dataframe(display_df, use_container_width=True, hide_index=True)
    else:
        st.info("아직 뱃속에 음식물이 없습니다.")

    st.divider()
    
    last_poop = user_data['last_poop']
    total_g = user_data['total_weight_in_stomach']
    next_time, hours, reason = predict_next_poop(last_poop, total_g)
    
    st.write(f"**🚽 마지막 배변:** {last_poop}")
    st.write(f"**⚖️ 현재 뱃속 무게:** {total_g}g (내가 먹은 양 합계)")
    st.write(f"**📝 예측 근거:** {reason}")
    
    now = datetime.datetime.now()
    diff = next_time - now
    
    st.subheader(f"🎯 다음 신호 예상: {next_time.strftime('%m월 %d일 %H시 %M분')}")
    
    if diff.total_seconds() > 0:
        d_hours = diff.seconds // 3600
        d_minutes = (diff.seconds % 3600) // 60
        st.success(f"약 **{diff.days * 24 + d_hours}시간 {d_minutes}분** 뒤에 신호가 올 것 같습니다!")
    else:
        st.error("이미 신호가 왔을 시간입니다! 🚨")