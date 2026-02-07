import streamlit as st
import google.generativeai as genai
import PIL.Image
import json
import datetime
import time
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import hashlib
import statistics
import os

# ---------------------------------------------------------
# [설정] API 키 & 구글 시트 연결
# ---------------------------------------------------------
# 1. Gemini API 설정
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"], transport='rest')
else:
    st.error("🚨 API 키가 없습니다. Secrets 설정을 확인해주세요.")
    st.stop()

model = genai.GenerativeModel('gemini-flash-latest')

# 2. 구글 시트 연결 함수 (키 자동 수리 기능 추가 🛠️)
@st.cache_resource
def get_google_sheet_client():
    try:
        if "gcp_service_account" in st.secrets:
            key_dict = dict(st.secrets["gcp_service_account"])
            
            # 🛠️ [핵심] private_key 자동 수리
            pk = key_dict.get("private_key", "")
            
            # 1. "..." 같은 예시 문구가 들어있으면 에러 띄우기
            if "..." in pk or len(pk) < 100:
                st.error("🚨 'private_key'가 너무 짧거나 '...'이 포함되어 있습니다! JSON 파일의 진짜 긴 암호를 복사해서 넣어주세요.")
                return None

            # 2. 줄바꿈 문자(\n) 처리 (문자열로 들어왔을 때)
            if "\\n" in pk:
                pk = pk.replace("\\n", "\n")
            
            # 3. 앞뒤 공백 제거 및 업데이트
            key_dict["private_key"] = pk.strip()

        elif "GOOGLE_SHEET_KEY" in st.secrets:
            key_dict = json.loads(st.secrets["GOOGLE_SHEET_KEY"])
        else:
            st.error("🚨 Secrets에 구글 시트 키가 없습니다. [gcp_service_account] 설정을 확인해주세요.")
            return None

        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"🔌 구글 시트 연결 실패: {e}\n(Secrets의 private_key 형식을 확인해주세요!)")
        return None

# 3. 데이터 로드/저장 함수
def get_or_create_worksheet(client, sheet_name, user_name):
    try:
        sh = client.open("poop_db")
    except gspread.SpreadsheetNotFound:
        st.error("🚨 'poop_db'라는 이름의 구글 스프레드시트를 찾을 수 없습니다! 구글 드라이브에서 파일을 만들고 봇 계정을 편집자로 초대했는지 확인해주세요.")
        st.stop()

    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=sheet_name, rows=100, cols=10)
        if sheet_name == "meals":
            worksheet.append_row(["이름", "날짜", "메뉴", "인원", "먹은양(g)", "배변변환량(g)"])
        elif sheet_name == "poops":
            worksheet.append_row(["이름", "날짜", "배출량(g)", "컨디션", "예측오차(분)", "예측시간"])
    
    return worksheet

def load_data_from_sheet(user_name):
    client = get_google_sheet_client()
    if not client: return [], [], 0.0

    # 1. 식사 기록
    ws_meals = get_or_create_worksheet(client, "meals", user_name)
    meals_data = ws_meals.get_all_records()
    my_meals = [m for m in meals_data if str(m.get("이름")) == user_name]

    # 2. 배변 기록
    ws_poops = get_or_create_worksheet(client, "poops", user_name)
    poops_data = ws_poops.get_all_records()
    my_poops = [p for p in poops_data if str(p.get("이름")) == user_name]

    # 3. 뱃속 재고 계산
    current_stock = 0.0
    events = []
    
    def safe_float(val):
        try: return float(val)
        except: return 0.0

    for m in my_meals:
        if m.get("날짜"):
            events.append({"type": "eat", "date": str(m["날짜"]), "amount": safe_float(m.get("배변변환량(g)", 0))})
    for p in my_poops:
        if p.get("날짜"):
            events.append({"type": "poop", "date": str(p["날짜"]), "amount": safe_float(p.get("배출량(g)", 0))})
    
    def safe_parse(d):
        try: return datetime.datetime.strptime(d, "%Y-%m-%d %H:%M")
        except: return datetime.datetime.min
    
    events.sort(key=lambda x: safe_parse(x["date"]))

    for event in events:
        if event["type"] == "eat":
            current_stock += event["amount"]
        elif event["type"] == "poop":
            current_stock -= event["amount"]
            if current_stock < 0: current_stock = 0.0

    return my_meals, my_poops, round(current_stock, 1)

def save_meal_to_sheet(user_name, date, menu, people, weight, poop_amount):
    client = get_google_sheet_client()
    if client:
        ws = get_or_create_worksheet(client, "meals", user_name)
        ws.append_row([user_name, date, menu, people, weight, poop_amount])

def save_poop_to_sheet(user_name, date, amount, condition, error_min, pred_time):
    client = get_google_sheet_client()
    if client:
        ws = get_or_create_worksheet(client, "poops", user_name)
        ws.append_row([user_name, date, amount, condition, error_min, pred_time])

# ---------------------------------------------------------
# 🕵️‍♂️ [비밀 공식] 배변량 계산
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
        p_r, f_r, c_r, fib_r, w_f, b_f = 0.1, 0.1, 0.2, 0.9, 2.33, 1.3

    solid_waste = (protein * p_r) + (fat * f_r) + (carbs * c_r) + (fiber * fib_r)
    total_poop = (solid_waste * w_f) * b_f
    return round(total_poop, 1)

# ---------------------------------------------------------
# AI 및 유틸리티 함수
# ---------------------------------------------------------
def analyze_food_image(image):
    image.thumbnail((512, 512)) 
    prompt = """
    이 음식 사진을 분석해서 JSON 형식으로만 답해줘.
    1. 음식 이름 (food_name): 메뉴명 (예: 김치찌개)
    2. 총 중량 (total_weight_g): 사진에 보이는 음식 전체 무게(g) 숫자만
    {
        "food_name": "음식 이름",
        "total_weight_g": 숫자,
        "comment": "짧은 평가"
    }
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = model.generate_content([prompt, image])
            text = response.text.replace("```json", "").replace("```", "").strip()
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start:end + 1]
                result = json.loads(text)
                if result.get("food_name") and result.get("total_weight_g"):
                    return result
            time.sleep(1)
        except:
            time.sleep(1)
    return None

def normalize_ai_result(raw):
    if not isinstance(raw, dict): return None, "AI 응답 형식 오류"
    name = str(raw.get("food_name", "")).strip()
    total = raw.get("total_weight_g", None)
    try:
        if isinstance(total, str): total = total.replace("g", "").strip()
        total = float(total)
    except: return None, "중량 숫자 변환 오류"
    return {"food_name": name, "total_weight_g": total, "comment": raw.get("comment", "")}, None

def parse_dt(value):
    try: return datetime.datetime.strptime(str(value), "%Y-%m-%d %H:%M")
    except: return None

def estimate_transit_hours(meals, poops):
    meals_f, poops_f = [], []
    for m in meals:
        dt = parse_dt(m.get("날짜"))
        if dt: meals_f.append({"_dt": dt})
    for p in poops:
        dt = parse_dt(p.get("날짜"))
        if dt: poops_f.append({"_dt": dt})
    
    meals_f.sort(key=lambda x: x["_dt"])
    poops_f.sort(key=lambda x: x["_dt"])

    if not meals_f or not poops_f: return None

    deltas = []
    recent_meals = meals_f[-5:]
    for m in recent_meals:
        for p in poops_f:
            if p["_dt"] > m["_dt"]:
                hours = (p["_dt"] - m["_dt"]).total_seconds() / 3600
                if 0.5 <= hours <= 72:
                    deltas.append(hours)
                break
    
    if len(deltas) < 1: return None
    return statistics.median(deltas)

def load_food_db():
    try:
        if os.path.exists("food_db.csv"):
            try:
                df = pd.read_csv("food_db.csv", encoding='utf-8')
            except:
                df = pd.read_csv("food_db.csv", encoding='euc-kr')

            df.columns = df.columns.str.strip()
            rename_map = {'식품명':'menu', '단백질(g)':'protein', '지방(g)':'fat', '탄수화물(g)':'carbs', '식이섬유(g)':'fiber', '메뉴':'menu'}
            for k, v in rename_map.items():
                if k in df.columns: df.rename(columns={k: v}, inplace=True)
            
            if 'menu' in df.columns:
                df = df.drop_duplicates(subset=['menu'])
                df = df.fillna(0)
                return df.set_index('menu').to_dict(orient='index')
    except: pass
    return {}

# ---------------------------------------------------------
# [UI 구성]
# ---------------------------------------------------------
st.set_page_config(page_title="나만의 비밀일기장 (클라우드)", page_icon="☁️")

if 'user_name' not in st.session_state:
    st.title("☁️ 나만의 비밀일기장 (구글 연동)")
    name_input = st.text_input("이름을 입력해주세요 (데이터 식별용)")
    if st.button("시작하기"):
        if name_input:
            st.session_state['user_name'] = name_input
            st.rerun()
    st.stop()

user_name = st.session_state['user_name']
food_db = load_food_db()

with st.spinner("☁️ 구글 시트에서 데이터를 불러오는 중..."):
    my_meals, my_poops, current_poop_stock = load_data_from_sheet(user_name)

st.title(f"🤫 {user_name}의 비밀일기장")

transit_hours = estimate_transit_hours(my_meals, my_poops)
last_meal_dt = parse_dt(my_meals[-1]["날짜"]) if my_meals else None
next_pred_dt = None
if transit_hours and last_meal_dt:
    next_pred_dt = last_meal_dt + datetime.timedelta(hours=transit_hours)

c1, c2, c3 = st.columns(3)
c1.metric("현재 뱃속 재고", f"{current_poop_stock:.1f}g")
c2.metric("내 소화 속도", f"{transit_hours:.1f}시간" if transit_hours else "기록 필요")
c3.metric("다음 배변 예상", next_pred_dt.strftime("%m-%d %H:%M") if next_pred_dt else "기록 필요")

tab1, tab2 = st.tabs(["🍽️ 식사 기록", "🧻 배변 기록"])

# --- 탭 1: 식사 기록 ---
with tab1:
    uploaded_file = st.file_uploader("식사 사진 업로드", type=['png', 'jpg', 'jpeg'])
    if uploaded_file:
        file_hash = hashlib.sha256(uploaded_file.getvalue()).hexdigest()
        if st.session_state.get("last_file_hash") != file_hash:
             st.session_state["last_file_hash"] = file_hash
             st.session_state.pop("ai_result", None)

        image = PIL.Image.open(uploaded_file)
        st.image(image, width=300)
        
        c1_t, c2_t = st.columns(2)
        input_date = c1_t.date_input("날짜", datetime.datetime.now())
        input_time = c2_t.time_input("시간", datetime.datetime.now())
        num_people = st.number_input("함께 먹은 인원", 1, 10, 1)

        if st.button("AI 분석 🚀"):
            with st.spinner("AI가 분석 중..."):
                res = analyze_food_image(image)
                if res:
                    norm, _ = normalize_ai_result(res)
                    if norm: st.session_state["ai_result"] = norm
                else:
                    st.error("분석 실패. 수동으로 입력해주세요.")

        if "ai_result" in st.session_state:
            data = st.session_state["ai_result"]
            st.info("결과를 확인하고 저장하세요.")
            name = st.text_input("메뉴명", data["food_name"])
            weight = st.number_input("총 중량(g)", value=float(data["total_weight_g"]))
            
            nut = {"protein": 5, "fat": 5, "carbs": 20, "fiber": 2}
            if name in food_db:
                nut = food_db[name]
                st.success(f"📚 DB 정보 적용: {name}")

            ratio = st.slider("내 섭취 비율", 0.1, 2.0, 1.0)
            my_weight = (weight * ratio) / num_people
            
            p = nut.get('protein', 0) * (my_weight/100)
            f = nut.get('fat', 0) * (my_weight/100)
            c = nut.get('carbs', 0) * (my_weight/100)
            fib = nut.get('fiber', 0) * (my_weight/100)
            poop_amt = calculate_poop_amount(p, f, c, fib)
            
            st.write(f"👉 **내 섭취량:** {my_weight:.1f}g | 💩 **예상 배변량:** +{poop_amt:.1f}g")

            if st.button("저장하기 💾"):
                dt_str = datetime.datetime.combine(input_date, input_time).strftime("%Y-%m-%d %H:%M")
                save_meal_to_sheet(user_name, dt_str, name, num_people, my_weight, poop_amt)
                st.success("구글 시트에 저장 완료!")
                st.session_state.pop("ai_result")
                time.sleep(1)
                st.rerun()

# --- 탭 2: 배변 기록 ---
with tab2:
    st.write("### 🚽 배변 기록")
    c1_p, c2_p = st.columns(2)
    p_date = c1_p.date_input("배변 날짜", datetime.datetime.now())
    p_time = c2_p.time_input("배변 시간", datetime.datetime.now())
    
    condition = st.radio("상태", ["🌟 쾌변 (100% 비움)", "🙂 보통 (50% 비움)", "😞 찜찜 (20% 비움)"], horizontal=True)
    
    if st.button("배변 기록 저장 💾", type="primary"):
        ratio = 1.0 if "쾌변" in condition else (0.5 if "보통" in condition else 0.2)
        out_amount = current_poop_stock * ratio
        
        dt_str = datetime.datetime.combine(p_date, p_time).strftime("%Y-%m-%d %H:%M")
        
        err_min = 0
        pred_str = ""
        if next_pred_dt:
            actual_dt = datetime.datetime.combine(p_date, p_time)
            err_min = int((actual_dt - next_pred_dt).total_seconds() / 60)
            pred_str = next_pred_dt.strftime("%Y-%m-%d %H:%M")

        save_poop_to_sheet(user_name, dt_str, out_amount, condition, err_min, pred_str)
        st.balloons()
        st.success(f"{out_amount:.1f}g 배출 기록 완료!")
        time.sleep(1)
        st.rerun()

    st.divider()
    if my_poops:
        df = pd.DataFrame(my_poops)
        if not df.empty:
            df = df.iloc[::-1]
            st.dataframe(df, use_container_width=True)
    else:
        st.info("기록이 없습니다.")
