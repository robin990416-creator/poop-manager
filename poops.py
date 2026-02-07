import streamlit as st
import google.generativeai as genai
import PIL.Image
import json
import datetime
import time
import pandas as pd
import os
import hashlib
import statistics

# ---------------------------------------------------------
# [설정] API 키 & 데이터 파일
# ---------------------------------------------------------
if "GOOGLE_API_KEY" in st.secrets:
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
else:
    st.error("🚨 API 키가 없습니다. Secrets 설정을 확인해주세요.")
    st.stop()

# 파일 설정
DATA_FILE = "user_health_data.json"
FOOD_DB_FILE = "food_db.csv"

genai.configure(api_key=GOOGLE_API_KEY, transport='rest')
model = genai.GenerativeModel('gemini-flash-latest')

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
        # 비상용 기본값
        p_r, f_r, c_r, fib_r, w_f, b_f = 0.1, 0.1, 0.2, 0.9, 2.33, 1.3

    solid_waste = (protein * p_r) + (fat * f_r) + (carbs * c_r) + (fiber * fib_r)
    total_poop = (solid_waste * w_f) * b_f
    
    return round(total_poop, 1)

# ---------------------------------------------------------
# 데이터 관리 함수 (중복 에러 완벽 해결 🛠️)
# ---------------------------------------------------------
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{DATA_FILE}.bak-{ts}"
            try:
                os.replace(DATA_FILE, backup_path)
            except Exception:
                pass
            return {"users": {}}
    return {"users": {}}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_food_db():
    if os.path.exists(FOOD_DB_FILE):
        try:
            # 1. 인코딩 자동 감지
            try:
                df = pd.read_csv(FOOD_DB_FILE, encoding='utf-8')
            except UnicodeDecodeError:
                try:
                    df = pd.read_csv(FOOD_DB_FILE, encoding='euc-kr')
                except UnicodeDecodeError:
                    df = pd.read_csv(FOOD_DB_FILE, encoding='cp949')

            # 2. 컬럼 이름 공백 제거
            df.columns = df.columns.str.strip()
            
            # 3. 공공데이터 컬럼명 매핑
            column_mapping = {
                '식품명': 'menu', '식품이름': 'menu', '메뉴': 'menu', '품목명': 'menu',
                '단백질(g)': 'protein', '단백질': 'protein',
                '지방(g)': 'fat', '지방': 'fat',
                '탄수화물(g)': 'carbs', '탄수화물': 'carbs',
                '식이섬유(g)': 'fiber', '식이섬유': 'fiber', '총식이섬유(g)': 'fiber'
            }
            df.rename(columns=column_mapping, inplace=True)

            if 'menu' in df.columns:
                # 🛠️ [핵심] 중복된 메뉴가 있으면 첫 번째만 남기고 삭제 (에러 원천 차단)
                df = df.drop_duplicates(subset=['menu'])
                
                fill_cols = ['protein', 'fat', 'carbs', 'fiber']
                for c in fill_cols:
                    if c in df.columns:
                        df[c] = df[c].fillna(0)
                    else:
                        df[c] = 0.0
                return df.set_index('menu').to_dict(orient='index')
            else:
                st.warning(f"CSV 파일 형식이 맞지 않습니다. '식품명' 열이 필요합니다.")
                return {}
        except Exception as e:
            st.warning(f"CSV 파일을 읽는 중 오류 발생: {e}")
            return {}
    return {}

# ---------------------------------------------------------
# AI 분석 함수 (5회 재시도 기능 포함 🔄)
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
    
    max_retries = 5
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
        except Exception as e:
            time.sleep(1)
            
    return None

def normalize_ai_result(raw):
    if not isinstance(raw, dict):
        return None, "AI 응답 형식을 확인할 수 없습니다."
    name = str(raw.get("food_name", "")).strip()
    total = raw.get("total_weight_g", None)
    try:
        if isinstance(total, str):
            total = total.replace("g", "").strip()
        total = float(total)
    except Exception:
        return None, "총 중량 값을 숫자로 해석할 수 없습니다."
    if not name:
        return None, "메뉴명을 확인할 수 없습니다."
    if total <= 0:
        return None, "총 중량은 0보다 커야 합니다."
    return {"food_name": name, "total_weight_g": total, "comment": raw.get("comment", "")}, None

def parse_dt(value):
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M")
    except Exception:
        return None

def estimate_transit_hours(meals, poops, window_days=3, max_hours=72):
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=window_days)
    meals_f = []
    for m in meals:
        dt = parse_dt(m.get("date", ""))
        if dt and dt >= cutoff:
            meals_f.append({**m, "_dt": dt})
    poops_f = []
    for p in poops:
        dt = parse_dt(p.get("date", ""))
        if dt and dt >= cutoff:
            poops_f.append({**p, "_dt": dt})
    meals_f.sort(key=lambda x: x["_dt"])
    poops_f.sort(key=lambda x: x["_dt"])
    if not meals_f or not poops_f:
        return None, {"meals": len(meals_f), "poops": len(poops_f), "samples": 0}

    deltas = []
    for meal in meals_f:
        mt = meal["_dt"]
        for p in poops_f:
            pt = p["_dt"]
            if pt >= mt:
                delta_h = (pt - mt).total_seconds() / 3600
                if 0 <= delta_h <= max_hours:
                    deltas.append(delta_h)
                break

    if len(deltas) < 3:
        return None, {"meals": len(meals_f), "poops": len(poops_f), "samples": len(deltas)}
    return statistics.median(deltas), {"meals": len(meals_f), "poops": len(poops_f), "samples": len(deltas)}

def get_latest_meal_dt(meals):
    dts = []
    for m in meals:
        dt = parse_dt(m.get("date", ""))
        if dt:
            dts.append(dt)
    return max(dts) if dts else None

# ---------------------------------------------------------
# [UI 구성]
# ---------------------------------------------------------
st.set_page_config(page_title="나만의 비밀일기장", page_icon="🤫")

if 'user_name' not in st.session_state:
    st.title("🤫 나만의 비밀일기장")
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
    data["users"][user_name] = {}

user_data = data["users"][user_name]
if "last_poop" not in user_data: user_data["last_poop"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
if "meals_log" not in user_data: user_data["meals_log"] = []
if "current_poop_stock" not in user_data: user_data["current_poop_stock"] = 0.0
if "poop_log" not in user_data: user_data["poop_log"] = []

st.title(f"🤫 {user_name}의 비밀일기장")

transit_hours, transit_stats = estimate_transit_hours(user_data["meals_log"], user_data["poop_log"])
latest_meal_dt = get_latest_meal_dt(user_data["meals_log"])
next_pred_dt = None
if transit_hours and latest_meal_dt:
    next_pred_dt = latest_meal_dt + datetime.timedelta(hours=transit_hours)

c1, c2, c3 = st.columns(3)
c1.metric(label="현재 뱃속 예상 배변량", value=f"{user_data['current_poop_stock']:.1f}g")
if transit_hours:
    c2.metric(label="개인화 소화시간(중앙값)", value=f"{transit_hours:.1f}시간", help="최근 3일 데이터 기반")
else:
    c2.metric(label="개인화 소화시간(중앙값)", value="기록 필요")

if next_pred_dt:
    c3.metric(label="다음 예상 배변 시각", value=next_pred_dt.strftime("%Y-%m-%d %H:%M"))
    if next_pred_dt < datetime.datetime.now():
        c3.caption("이미 지난 예측입니다.")
else:
    c3.metric(label="다음 예상 배변 시각", value="기록 필요")

if not transit_hours:
    st.info(f"개인화 예측을 위해 3회 이상 기록이 필요합니다. (현재: {transit_stats['samples']}회)")

with st.expander("🔎 예측 상세 보기"):
    st.write(f"- 최근 3일 식사: {transit_stats['meals']}건")
    st.write(f"- 최근 3일 배변: {transit_stats['poops']}건")
    if transit_hours:
        st.write(f"- 내 소화 속도: 약 {transit_hours:.1f}시간")

tab1, tab2 = st.tabs(["🍽️ 식사 기록", "🧻 배변 기록"])

# --- 탭 1: 식사 기록 ---
with tab1:
    uploaded_file = st.file_uploader("식사 사진 업로드", type=['png', 'jpg', 'jpeg'])
    if uploaded_file:
        file_bytes = uploaded_file.getvalue()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        if st.session_state.get("analysis_file_hash") != file_hash:
            st.session_state["analysis_file_hash"] = file_hash
            st.session_state.pop("analysis_result", None)
            st.session_state.pop("analysis_error", None)
            st.session_state.pop("confirmed_name", None)
            st.session_state.pop("confirmed_weight", None)

        image = PIL.Image.open(uploaded_file)
        st.image(image, width=300)
        
        st.write("---")
        c1, c2 = st.columns(2)
        input_date = c1.date_input("📅 날짜", datetime.datetime.now())
        input_time = c2.time_input("⏰ 시간", datetime.datetime.now())
        
        st.write("👥 **함께 먹은 사람은?**")
        num_people = st.number_input("총 인원 (나 포함)", min_value=1, value=1, step=1)
        
        if st.button("AI 분석 시작 🚀", type="primary"):
            status_text = st.empty()
            status_text.text("AI가 분석 중입니다... (1차 시도)")
            
            result = analyze_food_image(image)
            
            if result:
                status_text.success("분석 성공! ✅")
                normalized, err = normalize_ai_result(result)
                if normalized:
                    st.session_state['analysis_result'] = normalized
                    st.session_state.pop("analysis_error", None)
                    st.session_state['confirmed_name'] = normalized['food_name']
                    st.session_state['confirmed_weight'] = normalized['total_weight_g']
                else:
                    st.session_state['analysis_error'] = err
            else:
                status_text.error("분석 실패. 수동으로 입력해주세요. 😭")
                st.session_state['analysis_error'] = "분석 실패 (5회 재시도 초과)"

        if 'analysis_error' in st.session_state:
            st.error(st.session_state['analysis_error'])
            manual_name = st.text_input("메뉴명 (수동)", key="manual_name")
            manual_weight = st.number_input("중량(g) (수동)", value=300.0, step=10.0, key="manual_weight")
            if st.button("수동 입력 적용"):
                st.session_state['analysis_result'] = {
                    "food_name": manual_name,
                    "total_weight_g": manual_weight,
                    "comment": "수동 입력"
                }
                st.session_state['confirmed_name'] = manual_name
                st.session_state['confirmed_weight'] = manual_weight
                st.session_state.pop("analysis_error", None)
                st.rerun()

        if 'analysis_result' in st.session_state:
            st.divider()
            st.subheader("🧐 결과 확인 및 수정")
            st.info("내용이 맞는지 확인해주세요!")
            
            name = st.text_input("메뉴 이름", value=st.session_state.get('confirmed_name', ''), key='input_name')
            total_w = st.number_input("전체 중량(g)", value=float(st.session_state.get('confirmed_weight', 0)), step=10.0, key='input_weight')
            
            st.write(f"---")
            
            if name in food_db:
                nut = food_db[name]
                st.success(f"📚 DB 적용: {name}")
            else:
                st.warning(f"DB에 없음 (기본값)")
                nut = {"protein": 5, "fat": 5, "carbs": 20, "fiber": 2}

            eat_ratio = st.slider("내 섭취 비율", 0.5, 2.0, 1.0, 0.1)
            my_share_weight = (total_w * eat_ratio) / num_people
            
            st.write(f"👉 **내가 먹은 양:** {my_share_weight:.1f}g ({num_people}인)")
            
            p = nut.get('protein', 0) * (my_share_weight / 100)
            f = nut.get('fat', 0) * (my_share_weight / 100)
            c = nut.get('carbs', 0) * (my_share_weight / 100)
            fib = nut.get('fiber', 0) * (my_share_weight / 100)
            
            poop = calculate_poop_amount(p, f, c, fib)
            
            st.write(f"### 💩 예상 배변량: +{poop}g")
            
            if st.button("확인 완료 및 저장 💾", type="primary"):
                eat_datetime = datetime.datetime.combine(input_date, input_time)
                log = {
                    "date": eat_datetime.strftime("%Y-%m-%d %H:%M"),
                    "food": f"{name} ({num_people}인)",
                    "weight": round(my_share_weight, 1),
                    "poop": poop
                }
                user_data['meals_log'].append(log)
                user_data['current_poop_stock'] += poop
                save_data(data)
                
                del st.session_state['analysis_result']
                st.session_state.pop("confirmed_name", None)
                st.session_state.pop("confirmed_weight", None)
                
                st.toast("저장 완료!")
                time.sleep(1)
                st.rerun()

# --- 탭 2: 배변 기록 (빈 화면 해결됨 ✅) ---
with tab2:
    st.write("🧻 **배변 기록**")
    
    st.write("### 🚀 지금 바로 쾌변하셨나요?")
    if st.button("네! 지금 다 비웠습니다 🚽", type="primary"):
        dump_amount = float(user_data['current_poop_stock'])
        now = datetime.datetime.now()
        entry = {"date": now.strftime("%Y-%m-%d %H:%M"), "amount": round(dump_amount, 1)}
        
        if next_pred_dt:
            err = int((now - next_pred_dt).total_seconds() / 60)
            entry["predicted"] = next_pred_dt.strftime("%Y-%m-%d %H:%M")
            entry["error_min"] = err
            
        user_data['poop_log'].append(entry)
        user_data['current_poop_stock'] = 0.0
        user_data['last_poop'] = now.strftime("%Y-%m-%d %H:%M")
        save_data(data)
        st.balloons()
        st.success(f"상쾌하시겠어요! (예상 배출량: {dump_amount:.1f}g)")
        time.sleep(1)
        st.rerun()

    st.divider()

    st.write("### 🕒 아까 다녀오셨나요?")
    c1, c2 = st.columns(2)
    poop_date = c1.date_input("날짜", datetime.datetime.now(), key="pd")
    poop_time = c2.time_input("시간", datetime.datetime.now(), key="pt")
    
    if st.button("이 시간에 다녀왔다고 기록하기 💾"):
        dump_amount = float(user_data['current_poop_stock'])
        poop_dt = datetime.datetime.combine(poop_date, poop_time)
        
        entry = {"date": poop_dt.strftime("%Y-%m-%d %H:%M"), "amount": round(dump_amount, 1)}
        
        if next_pred_dt:
            err = int((poop_dt - next_pred_dt).total_seconds() / 60)
            entry["predicted"] = next_pred_dt.strftime("%Y-%m-%d %H:%M")
            entry["error_min"] = err
        
        user_data['poop_log'].append(entry)
        user_data['current_poop_stock'] = 0.0
        user_data['last_poop'] = poop_dt.strftime("%Y-%m-%d %H:%M")
