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
# 데이터 관리 함수
# ---------------------------------------------------------
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            # 손상된 JSON 복구: 백업 후 초기화
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{DATA_FILE}.bak-{ts}"
            try:
                os.replace(DATA_FILE, backup_path)
            except Exception:
                pass
            st.warning("저장 데이터가 손상되어 초기화했습니다. 백업 파일을 확인해주세요.")
            return {"users": {}}
    return {"users": {}}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_food_db():
    if os.path.exists(FOOD_DB_FILE):
        try:
            df = pd.read_csv(FOOD_DB_FILE)
            # 메뉴명을 키로 변환
            return df.set_index('menu').to_dict(orient='index')
        except Exception as e:
            st.warning(f"CSV 파일을 읽을 수 없습니다: {e}")
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
    2. 총 중량 (total_weight_g): 사진에 보이는 음식 전체 무게(g)
    {
        "food_name": "음식 이름",
        "total_weight_g": 숫자,
        "comment": "짧은 평가"
    }
    """
    try:
        response = model.generate_content([prompt, image])
        text = response.text.replace("```json", "").replace("```", "").strip()
        # JSON 블록만 추출 시도
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
        return json.loads(text)
    except:
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

def get_latest_meal_dt(meals):
    dts = []
    for m in meals:
        dt = parse_dt(m.get("date", ""))
        if dt:
            dts.append(dt)
    return max(dts) if dts else None

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

# 사용자 데이터 초기화 (오류 방지 코드 포함)
if user_name not in data["users"]:
    data["users"][user_name] = {}

user_data = data["users"][user_name]
# 필수 키가 없으면 생성 (구버전 호환용)
if "last_poop" not in user_data: user_data["last_poop"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
if "meals_log" not in user_data: user_data["meals_log"] = []
if "current_poop_stock" not in user_data: user_data["current_poop_stock"] = 0.0
if "poop_log" not in user_data: user_data["poop_log"] = []

st.title(f"🚽 {user_name}님의 장 건강 매니저")

transit_hours, transit_stats = estimate_transit_hours(user_data["meals_log"], user_data["poop_log"])
latest_meal_dt = get_latest_meal_dt(user_data["meals_log"])
next_pred_dt = None
if transit_hours and latest_meal_dt:
    next_pred_dt = latest_meal_dt + datetime.timedelta(hours=transit_hours)

c1, c2, c3 = st.columns(3)
c1.metric(label="현재 뱃속 예상 배변량", value=f"{user_data['current_poop_stock']:.1f}g")
if transit_hours:
    c2.metric(label="개인화 소화시간(중앙값)", value=f"{transit_hours:.1f}시간", help="최근 3일 기록을 기반으로 계산합니다.")
else:
    c2.metric(label="개인화 소화시간(중앙값)", value="기록 필요")

if next_pred_dt:
    c3.metric(label="다음 예상 배변 시각", value=next_pred_dt.strftime("%Y-%m-%d %H:%M"), help="가장 최근 식사 기준 예측입니다.")
    if next_pred_dt < datetime.datetime.now():
        c3.caption("현재 시각 기준 이미 지난 예측입니다. 최신 식사를 기록해 주세요.")
else:
    c3.metric(label="다음 예상 배변 시각", value="기록 필요")

if not transit_hours:
    st.info(f"개인화 소화시간을 계산하려면 최근 3일 기록이 필요합니다. (최근 3일 식사 {transit_stats['meals']}건, 배변 {transit_stats['poops']}건, 샘플 {transit_stats['samples']}건)")

with st.expander("🔎 예측 상세 보기"):
    st.write("**계산 기준**: 최근 3일 기록 중 ‘식사 후 첫 배변까지 시간’의 중앙값")
    st.write(f"- 최근 3일 식사: {transit_stats['meals']}건")
    st.write(f"- 최근 3일 배변: {transit_stats['poops']}건")
    st.write(f"- 유효 샘플: {transit_stats['samples']}건")
    if latest_meal_dt:
        st.write(f"- 가장 최근 식사: {latest_meal_dt.strftime('%Y-%m-%d %H:%M')}")
    if transit_hours and next_pred_dt:
        st.write(f"- 개인화 소화시간: {transit_hours:.1f}시간")
        st.write(f"- 다음 예상 배변 시각: {next_pred_dt.strftime('%Y-%m-%d %H:%M')}")
    else:
        st.caption("기록이 충분하지 않으면 예측이 표시되지 않습니다.")

tab1, tab2 = st.tabs(["🍽️ 식사 기록", "💩 배변 기록"])

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

        image = PIL.Image.open(uploaded_file)
        st.image(image, width=300)
        
        st.write("---")
        # 👇 [복구됨] 시간 선택 + 인원 수 선택
        c1, c2 = st.columns(2)
        input_date = c1.date_input("📅 날짜", datetime.datetime.now())
        input_time = c2.time_input("⏰ 시간", datetime.datetime.now())
        
        st.write("👥 **함께 먹은 사람은?**")
        num_people = st.number_input("총 인원 (나 포함)", min_value=1, value=1, step=1)
        
        if st.button("AI 분석 시작 🚀", type="primary"):
            with st.spinner('분석 중...'):
                result = analyze_food_image(image)
                if result:
                    normalized, err = normalize_ai_result(result)
                    if normalized:
                        st.session_state['analysis_result'] = normalized
                        st.session_state.pop("analysis_error", None)
                    else:
                        st.session_state['analysis_error'] = err
                else:
                    st.session_state['analysis_error'] = "AI 분석 결과를 가져오지 못했습니다."
                    st.session_state.pop("analysis_result", None)
        
        # 분석 결과 표시
        if 'analysis_error' in st.session_state:
            st.error(st.session_state['analysis_error'])
            st.write("수동으로 입력해 주세요.")
            manual_name = st.text_input("메뉴명 (수동 입력)", key="manual_food_name")
            manual_weight = st.number_input("총 중량(g) (수동 입력)", min_value=1.0, value=300.0, step=1.0, key="manual_total_weight")
            if st.button("수동 입력 적용"):
                if manual_name.strip():
                    st.session_state['analysis_result'] = {
                        "food_name": manual_name.strip(),
                        "total_weight_g": float(manual_weight),
                        "comment": ""
                    }
                    st.session_state.pop("analysis_error", None)
                else:
                    st.warning("메뉴명을 입력해주세요.")

        if 'analysis_result' in st.session_state:
            res = st.session_state['analysis_result']
            name = res['food_name']
            total_w = res['total_weight_g'] # 전체 무게
            
            st.success(f"🔍 메뉴: **{name}** (전체 약 {total_w}g)")
            with st.expander("✏️ 결과 수정"):
                edit_name = st.text_input("메뉴명 수정", value=name, key="edit_food_name")
                edit_weight = st.number_input("총 중량(g) 수정", min_value=1.0, value=float(total_w), step=1.0, key="edit_total_weight")
                if st.button("수정 적용"):
                    if edit_name.strip():
                        st.session_state['analysis_result'] = {
                            "food_name": edit_name.strip(),
                            "total_weight_g": float(edit_weight),
                            "comment": res.get("comment", "")
                        }
                        st.toast("수정이 적용되었습니다!")
                        st.rerun()
                    else:
                        st.warning("메뉴명을 입력해주세요.")
            
            # DB 매칭
            if name in food_db:
                nut = food_db[name]
                st.info("📚 데이터베이스(CSV) 정보를 사용합니다!")
            else:
                st.warning("DB에 없는 메뉴입니다. (기본값 적용)")
                nut = {"protein": 5, "fat": 5, "carbs": 20, "fiber": 2}

            # 섭취 비율 조절 (내가 얼마나 먹었나)
            eat_ratio = st.slider("내 섭취 비율 (1.0 = 1인분)", 0.5, 2.0, 1.0, 0.1)
            
            # 🧮 [핵심] 내 몫 계산 (전체 무게 * 내 비율 / 인원수)
            my_share_weight = (total_w * eat_ratio) / num_people
            
            st.write(f"👉 **내가 먹은 양:** 약 {my_share_weight:.1f}g ({num_people}명이서 나눠 먹음)")
            
            # 영양소 계산 (내 몫 기준)
            p = nut['protein'] * (my_share_weight / 100)
            f = nut['fat'] * (my_share_weight / 100)
            c = nut['carbs'] * (my_share_weight / 100)
            fib = nut['fiber'] * (my_share_weight / 100)
            
            # 배변량 계산
            poop = calculate_poop_amount(p, f, c, fib)
            
            st.write(f"### 💩 예상 배변량: +{poop}g")

            eat_datetime = datetime.datetime.combine(input_date, input_time)
            if transit_hours:
                predict_dt = eat_datetime + datetime.timedelta(hours=transit_hours)
                st.write(f"⏳ **개인화 예측 배변 시각:** {predict_dt.strftime('%Y-%m-%d %H:%M')}")
            else:
                st.caption("개인화 예측은 최근 3일 기록이 쌓이면 제공됩니다.")
            
            if st.button("저장하기 💾"):
                log = {
                    "date": eat_datetime.strftime("%Y-%m-%d %H:%M"),
                    "food": f"{name} ({num_people}인 식사)",
                    "weight": round(my_share_weight, 1),
                    "poop": poop
                }
                user_data['meals_log'].append(log)
                user_data['current_poop_stock'] += poop
                save_data(data)
                
                del st.session_state['analysis_result']
                st.toast("저장되었습니다!")
                time.sleep(1)
                st.rerun()

# --- 탭 2: 배변 기록 ---
with tab2:
    st.write("🧻 **배변 기록**")
    c1, c2 = st.columns(2)
    poop_date = c1.date_input("📅 날짜 (배변)", datetime.datetime.now(), key="poop_date")
    poop_time = c2.time_input("⏰ 시간 (배변)", datetime.datetime.now(), key="poop_time")
    poop_amount = st.number_input(
        "배변량(g) (기본: 현재 추정치)",
        min_value=0.0,
        value=float(user_data['current_poop_stock']),
        step=1.0,
        key="poop_amount"
    )

    st.write("🚀 **빠른 기록 (현재 시각 기준)**")
    if st.button("쾌변 완료 🚽"):
        now = datetime.datetime.now()
        dump_amount = float(user_data['current_poop_stock'])
        entry = {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "amount": round(dump_amount, 1)
        }
        if next_pred_dt:
            error_min = int((now - next_pred_dt).total_seconds() / 60)
            entry["predicted"] = next_pred_dt.strftime("%Y-%m-%d %H:%M")
            entry["error_min"] = error_min
        user_data['poop_log'].append(entry)

        user_data['current_poop_stock'] = 0.0
        user_data['last_poop'] = now.strftime("%Y-%m-%d %H:%M")
        save_data(data)

        if next_pred_dt:
            err_min = entry["error_min"]
            abs_err = abs(err_min)
            if abs_err <= 60:
                st.success(f"예측과 거의 일치합니다! (오차 {err_min:+d}분)")
            else:
                st.info(f"예측과의 차이: {err_min:+d}분")
        else:
            st.info("예측값이 없어 정확도 비교는 생략되었습니다.")

        st.balloons()
        time.sleep(1)
        st.rerun()

    if st.button("배변 기록 저장 🚽", type="primary"):
        poop_datetime = datetime.datetime.combine(poop_date, poop_time)
        dump_amount = float(poop_amount)
        entry = {
            "date": poop_datetime.strftime("%Y-%m-%d %H:%M"),
            "amount": round(dump_amount, 1)
        }
        if next_pred_dt:
            error_min = int((poop_datetime - next_pred_dt).total_seconds() / 60)
            entry["predicted"] = next_pred_dt.strftime("%Y-%m-%d %H:%M")
            entry["error_min"] = error_min
        user_data['poop_log'].append(entry)

        # 현재 추정치에서 차감
        if dump_amount >= user_data['current_poop_stock']:
            user_data['current_poop_stock'] = 0.0
        else:
            user_data['current_poop_stock'] = round(user_data['current_poop_stock'] - dump_amount, 1)

        user_data['last_poop'] = poop_datetime.strftime("%Y-%m-%d %H:%M")
        save_data(data)
        st.balloons()
        st.success(f"배변 기록 완료: {dump_amount:.1f}g")
        time.sleep(1)
        st.rerun()

    if st.button("현재 추정치 전부 비우기 (즉시) 💨"):
        dump_amount = user_data['current_poop_stock']
        now = datetime.datetime.now()
        entry = {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "amount": round(dump_amount, 1)
        }
        if next_pred_dt:
            error_min = int((now - next_pred_dt).total_seconds() / 60)
            entry["predicted"] = next_pred_dt.strftime("%Y-%m-%d %H:%M")
            entry["error_min"] = error_min
        user_data['poop_log'].append(entry)
        user_data['current_poop_stock'] = 0.0
        user_data['last_poop'] = now.strftime("%Y-%m-%d %H:%M")
        save_data(data)
        st.balloons()
        st.success(f"시원하게 {dump_amount:.1f}g 배출 완료!")
        time.sleep(1)
        st.rerun()
    
    st.divider()
    st.write("📝 **최근 식사 내역**")
    
    if user_data['meals_log']:
        # 데이터프레임으로 이쁘게 보여주기
        df = pd.DataFrame(user_data['meals_log'])
        # 최신순 정렬
        df = df.iloc[::-1]
        
        # 컬럼 이름 한글로 변경
        df.columns = ['시간', '메뉴', '먹은양(g)', '배변량(g)']
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.info("아직 기록이 없습니다. 맛있는 걸 드시고 기록해보세요! 🍚")

    st.divider()
    st.write("📝 **최근 배변 내역**")
    if user_data['poop_log']:
        poop_df = pd.DataFrame(user_data['poop_log'])
        poop_df = poop_df.iloc[::-1]
        poop_df = poop_df.rename(columns={
            "date": "시간",
            "amount": "배변량(g)",
            "predicted": "예측시각",
            "error_min": "오차(분)"
        })
        display_cols = [c for c in ["시간", "배변량(g)", "예측시각", "오차(분)"] if c in poop_df.columns]
        st.dataframe(poop_df[display_cols], hide_index=True, use_container_width=True)
    else:
        st.info("아직 배변 기록이 없습니다. 기록을 추가해보세요! 🚽")
