import streamlit as st
import google.generativeai as genai
import PIL.Image
import json
import datetime
import time
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

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

# 2. 구글 시트 연결 함수 (캐싱으로 속도 향상)
@st.cache_resource
def get_google_sheet_client():
    try:
        # Secrets에서 JSON 문자열을 가져와서 딕셔너리로 변환
        key_dict = json.loads(st.secrets["GOOGLE_SHEET_KEY"])
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"🔌 구글 시트 연결 실패: {e}")
        return None

# 3. 데이터 로드/저장 함수 (구글 시트 버전)
def get_or_create_worksheet(client, sheet_name, user_name):
    # 시트 파일 열기 (이름: poop_db)
    try:
        sh = client.open("poop_db")
    except gspread.SpreadsheetNotFound:
        st.error("🚨 'poop_db'라는 이름의 구글 스프레드시트를 찾을 수 없습니다! 구글 드라이브에서 파일을 만들고 봇 계정을 초대했는지 확인해주세요.")
        st.stop()

    # 탭(Worksheet) 확인 및 생성
    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        # 탭이 없으면 새로 생성 (헤더 추가)
        worksheet = sh.add_worksheet(title=sheet_name, rows=100, cols=10)
        if sheet_name == "meals":
            worksheet.append_row(["이름", "날짜", "메뉴", "인원", "먹은양(g)", "배변변환량(g)"])
        elif sheet_name == "poops":
            worksheet.append_row(["이름", "날짜", "배출량(g)", "컨디션", "예측오차(분)", "예측시간"])
    
    return worksheet

def load_data_from_sheet(user_name):
    client = get_google_sheet_client()
    if not client: return [], [], 0.0

    # 1. 식사 기록 가져오기
    ws_meals = get_or_create_worksheet(client, "meals", user_name)
    meals_data = ws_meals.get_all_records()
    # 내 이름 데이터만 필터링
    my_meals = [m for m in meals_data if str(m.get("이름")) == user_name]

    # 2. 배변 기록 가져오기
    ws_poops = get_or_create_worksheet(client, "poops", user_name)
    poops_data = ws_poops.get_all_records()
    my_poops = [p for p in poops_data if str(p.get("이름")) == user_name]

    # 3. 현재 뱃속 재고 계산 (처음부터 다시 계산)
    current_stock = 0.0
    
    # 시간순 정렬을 위해 리스트 합치기 및 정렬
    events = []
    for m in my_meals:
        events.append({"type": "eat", "date": m["날짜"], "amount": float(m["배변변환량(g)"])})
    for p in my_poops:
        events.append({"type": "poop", "date": p["날짜"], "amount": float(p["배출량(g)"])})
    
    # 날짜 기준 오름차순 정렬
    events.sort(key=lambda x: datetime.datetime.strptime(x["date"], "%Y-%m-%d %H:%M"))

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
    # 구글 시트 데이터 포맷에 맞춰 변환
    meals_f, poops_f = [], []
    for m in meals:
        dt = parse_dt(m["날짜"])
        if dt: meals_f.append({"_dt": dt})
    for p in poops:
        dt = parse_dt(p["날짜"])
        if dt: poops_f.append({"_dt": dt})
    
    meals_f.sort(key=lambda x: x["_dt"])
    poops_f.sort(key=lambda x: x["_dt"])

    if not meals_f or not poops_f: return None

    deltas = []
    # 간단한 로직: 식사 후 가장 가까운 미래의 배변 시간 차이 (최근 5건만)
    recent_meals = meals_f[-5:]
    for m in recent_meals:
        for p in poops_f:
            if p["_dt"] > m["_dt"]:
                hours = (p["_dt"] - m["_dt"]).total_seconds() / 3600
                if 0.5 <= hours <= 72: # 유효 범위
                    deltas.append(hours)
                break
    
    if len(deltas) < 1: return None
    import statistics
    return statistics.median(deltas)

def load_food_db():
    try:
        if os.path.exists("food_db.csv"):
            df = pd.read_csv("food_db.csv") # 인코딩 이슈시 'euc-kr' 추가
            df.columns = df.columns.str.strip()
            # 간단 매핑
            rename_map = {'식품명':'menu', '단백질(g)':'protein', '지방(g)':'fat', '탄수화물(g)':'carbs', '식이섬유(g)':'fiber'}
            for k, v in rename_map.items():
                if k in df.columns: df.rename(columns={k: v}, inplace=True)
            
            if 'menu' in df.columns:
                 # 중복제거
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

# 데이터 로드 (구글 시트에서!)
with st.spinner("☁️ 구글 시트에서 데이터를 불러오는 중..."):
    my_meals, my_poops, current_poop_stock = load_data_from_sheet(user_name)

st.title(f"🤫 {user_name}의 비밀일기장")

# 통계 계산
transit_hours = estimate_transit_hours(my_meals, my_poops)
last_meal_dt = parse_dt(my_meals[-1]["날짜"]) if my_meals else None
next_pred_dt = last_meal_dt + datetime.timedelta(hours=transit_hours) if (transit_hours and last_meal_dt) else None

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

        # 분석 결과 확인 및 저장
        if "ai_result" in st.session_state:
            data = st.session_state["ai_result"]
            st.info("결과를 확인하고 저장하세요.")
            name = st.text_input("메뉴명", data["food_name"])
            weight = st.number_input("총 중량(g)", value=float(data["total_weight_g"]))
            
            # DB 영양소 확인
            nut = {"protein": 5, "fat": 5, "carbs": 20, "fiber": 2} # 기본값
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
        # 현재 재고 기반 배출량 계산
        ratio = 1.0 if "쾌변" in condition else (0.5 if "보통" in condition else 0.2)
        out_amount = current_poop_stock * ratio
        
        dt_str = datetime.datetime.combine(p_date, p_time).strftime("%Y-%m-%d %H:%M")
        
        # 오차 계산
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
        # 역순 정렬 (최신이 위로)
        df = df.iloc[::-1]
        st.dataframe(df, use_container_width=True)
    else:
        st.info("기록이 없습니다.")
