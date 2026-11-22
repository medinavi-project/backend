import os
import re
import sys
import html
import json
import numpy as np
import requests
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from openai import OpenAI

from app.hospital.dept_codes import resolve_department
from app.triage.chatbot import (
    CONDITION_KEY,
    DEFAULT_REGION,
    generate_final_analysis,
    has_sufficient_info,
    init_state,
    run_triage_turn,
    translate_titles_to_korean,
    validate_backend_response,
)

load_dotenv()
CLOVA_API_KEY = os.getenv("CLOVASTUDIO_API_KEY")
NMC_API_KEY = os.getenv("NMC_API_KEY")

client = OpenAI(
    api_key=CLOVA_API_KEY or "unset",  # MediNavi: import must not fail without a key
    base_url="https://clovastudio.stream.ntruss.com/v1/openai",
)

MEDLINEPLUS_API_URL = "https://wsearch.nlm.nih.gov/ws/query"
_MEDLINEPLUS_CACHE = {}

# 진료과 코드는 hospital/dept_codes.py 한 곳에서 관리한다


def _clean_html(raw_text):
    if not raw_text:
        return ""
    unescaped = html.unescape(raw_text)
    no_tags = re.sub(r"<[^>]+>", " ", unescaped)
    return re.sub(r"\s+", " ", no_tags).strip()


def get_embedding(text):
    url = "https://clovastudio.stream.ntruss.com/v1/openai/embeddings"
    headers = {
        "Authorization": f"Bearer {CLOVA_API_KEY}",
        "Content-Type": "application/json",
    }
    res = requests.post(url, headers=headers, json={"model": "bge-m3", "input": text}, timeout=15)
    if res.status_code != 200:
        raise RuntimeError(f"임베딩 API 오류 ({res.status_code}): {res.text}")
    return np.array(res.json()["data"][0]["embedding"], dtype=np.float32)


def cosine_similarity(vec_a, vec_b):
    denom = float(np.linalg.norm(vec_a) * np.linalg.norm(vec_b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / denom)


def _build_korean_symptom_text(state):
    parts = []
    if state.get("main_symptom"):
        parts.append(f"주증상: {state['main_symptom']}")
    if state.get("body_location"):
        parts.append(f"부위: {state['body_location']}")
    if state.get("onset"):
        parts.append(f"발생시점: {state['onset']}")
    if state.get("severity") is not None:
        parts.append(f"강도: {state['severity']}/10")
    if state.get("additional_symptoms"):
        parts.append(f"동반증상: {', '.join(state['additional_symptoms'])}")
    condition = state.get(CONDITION_KEY)
    if parts and condition:
        label = "기저질환" if condition.get("certainty") == "diagnosed" else "환자가 의심한 질환"
        parts.append(f"{label}: {condition['name']}")
    return " / ".join(parts)


def generate_medlineplus_queries(state):
    symptom_text = _build_korean_symptom_text(state)
    if not symptom_text:
        return []

    prompt = f"""
환자의 한국어 증상을 보고, 미국 MedlinePlus Health Topics의 공식 영문 주제명(표준 의학 용어)으로 검색할 키워드 3개를 골라줘.
반드시 JSON 문자열 배열만 출력하고 다른 말은 쓰지 마.

[규칙]
- 첫 번째 키워드: 주증상과 부위를 나타내는 표준 영문 의학 용어 (예: Abdominal Pain, Headache, Chest Pain, Sore Throat, Fever)
- 두 번째~세 번째 키워드: 해당 증상 조합에서 흔히 의심되는 대표 질환의 표준 영문명 (예: Appendicitis, Gastroenteritis, Gastritis, Peptic Ulcer, Migraine, Tonsillitis)
- stomachache 같은 비표준 일상어나 단순 pain, ache는 절대 쓰지 마.

[예시 입력] 주증상: 배 아픔 / 부위: 오른쪽 아랫배 / 동반증상: 발열, 움직일 때 통증
[예시 출력] ["Abdominal Pain", "Appendicitis", "Gastroenteritis"]

[환자 증상]
{symptom_text}
"""
    try:
        res = client.chat.completions.create(
            model="HCX-005",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw = (res.choices[0].message.content or "[]").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end + 1]
        queries = json.loads(raw)
        if isinstance(queries, list):
            clean_queries = [str(q).strip() for q in queries if str(q).strip()]
            if clean_queries:
                return clean_queries[:3]
    except Exception:
        pass

    # 검색어를 못 만들면 검색하지 않는다. (예전에는 "Abdominal Pain"으로 대신 검색해서
    #  두통 환자에게 복통·충수염 문서가 근거로 붙는 문제가 있었음 → 근거 부족으로 처리됨)
    return []


def _request_medlineplus(term, retmax=6):
    cache_key = (term.lower(), retmax)
    if cache_key in _MEDLINEPLUS_CACHE:
        return _MEDLINEPLUS_CACHE[cache_key]

    params = {
        "db": "healthTopics",
        "term": term,
        "retmax": retmax,
        "rettype": "brief",
        "tool": "medical_triage_chatbot",
    }
    res = requests.get(MEDLINEPLUS_API_URL, params=params, timeout=15)
    res.raise_for_status()

    root = ET.fromstring(res.content)
    docs = []
    seen_urls = set()

    for doc_el in root.findall(".//list/document"):
        url = (doc_el.attrib.get("url") or "").strip()
        if url and url in seen_urls:
            continue

        title = ""
        full_summary = ""
        snippet = ""
        groups = []

        for c in doc_el.findall("content"):
            cname = (c.attrib.get("name") or "").lower()
            text_val = _clean_html("".join(c.itertext()))
            if not text_val:
                continue
            if cname == "title" and not title:
                title = text_val
            elif cname == "fullsummary" and not full_summary:
                full_summary = text_val
            elif cname == "snippet" and not snippet:
                snippet = text_val
            elif cname == "groupname":
                groups.append(text_val)

        summary_text = (full_summary or snippet)[:900]
        if title and summary_text:
            docs.append({
                "title": title,
                "url": url,
                "summary": summary_text,
                "groups": groups[:3],
            })
            if url:
                seen_urls.add(url)

    _MEDLINEPLUS_CACHE[cache_key] = docs
    return docs


def fetch_medlineplus_docs(queries, retmax=6):
    if not queries:
        return []

    collected = []
    seen_urls = set()

    for q in queries[:3]:
        try:
            docs = _request_medlineplus(f'title:"{q}"', retmax=3)
            for d in docs:
                key = d["url"] or d["title"]
                if key not in seen_urls:
                    seen_urls.add(key)
                    collected.append(d)
        except Exception:
            continue

    if len(collected) >= 2:
        return collected[:retmax]

    for q in queries[:3]:
        try:
            docs = _request_medlineplus(f'"{q}"', retmax=4)
            for d in docs:
                key = d["url"] or d["title"]
                if key not in seen_urls:
                    seen_urls.add(key)
                    collected.append(d)
        except Exception:
            continue
        if len(collected) >= retmax:
            break

    return collected[:retmax]


def retrieve_medlineplus_rag(state, verbose=False):
    korean_query = _build_korean_symptom_text(state)
    if not korean_query:
        return {"queries": [], "medlineplus_docs": []}

    en_queries = generate_medlineplus_queries(state)

    raw_docs = fetch_medlineplus_docs(en_queries, retmax=6)
    if not raw_docs:
        if verbose:
            print("\n[MedlinePlus.gov 실시간 API 조회] 일치하는 문서를 찾지 못했습니다.")
        return {"queries": en_queries, "medlineplus_docs": []}

    try:
        query_vec = get_embedding(korean_query)
        scored = []
        for doc in raw_docs:
            doc_text = f"Title: {doc['title']} / Summary: {doc['summary']}"
            doc_vec = get_embedding(doc_text)
            score = cosine_similarity(query_vec, doc_vec)
            scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_docs = []
        for score, doc in scored[:3]:
            enriched = dict(doc)
            enriched["similarity_score"] = round(score, 4)
            top_docs.append(enriched)
    except Exception:
        top_docs = [dict(d) for d in raw_docs[:3]]

    # MedlinePlus 영문 문서 제목을 한국어 병명/증상명으로 번역
    ko_titles = translate_titles_to_korean([d["title"] for d in top_docs])
    for doc, ko_title in zip(top_docs, ko_titles):
        doc["title_ko"] = ko_title

    if verbose:
        print("\n[MedlinePlus.gov RAG 유사도 탐색 결과 (출처: MedlinePlus.gov)]")
        for idx, d in enumerate(top_docs, 1):
            display_name = d.get("title_ko") or d["title"]
            score_str = f" (유사도: {d['similarity_score']})" if "similarity_score" in d else ""
            print(f"- {idx}순위: {display_name}{score_str} | {d['url']}")

    return {
        "queries": en_queries,
        "medlineplus_docs": top_docs,
    }


def _resolve_dept_code(department_name):
    # 모르는 진료과를 내과(D001)로 바꿔 검색하지 않는다
    resolved = resolve_department(department_name or "")
    return resolved[1] if resolved else None


def find_hospitals_from_nmc(region_text, department_name):
    if not department_name or not NMC_API_KEY:
        return []
    safe_region = region_text or DEFAULT_REGION
    parts = safe_region.split()
    q0 = parts[0] if len(parts) >= 1 else "경기도"
    q1 = parts[1] if len(parts) >= 2 else "시흥시"
    qd = _resolve_dept_code(department_name)
    if not qd:
        return []

    list_url = "https://apis.data.go.kr/B552657/HsptlAsembySearchService/getHsptlMdcncListInfoInqire"
    params = {
        "serviceKey": NMC_API_KEY,
        "Q0": q0,
        "Q1": q1,
        "QD": qd,
        "pageNo": 1,
        "numOfRows": 5,
    }
    try:
        res = requests.get(list_url, params=params, timeout=10)
        root = ET.fromstring(res.content)
    except Exception:
        return []

    hospitals = []
    bass_url = "https://apis.data.go.kr/B552657/HsptlAsembySearchService/getHsptlBassInfoInqire"

    for item in root.findall(".//item"):
        hpid = item.findtext("hpid")
        duty_name = item.findtext("dutyName")
        dept_list_str = department_name

        if hpid:
            try:
                bass_res = requests.get(bass_url, params={"serviceKey": NMC_API_KEY, "HPID": hpid}, timeout=10)
                bass_root = ET.fromstring(bass_res.content)
                dgid = bass_root.findtext(".//dgidIdName")
                if dgid:
                    dept_list_str = dgid.strip()
            except Exception:
                pass

        hospitals.append({
            "기관명": duty_name.strip() if duty_name else "이름 없음",
            "진료과목": dept_list_str,
        })
    return hospitals


def process_turn_for_backend(user_message, current_state, turn_count, max_turns=5, verbose=False):
    payload = run_triage_turn(
        user_message=user_message,
        current_state=current_state,
        turn_count=turn_count,
        max_turns=max_turns,
    )

    if payload["action"] == "COMPLETE":
        last_updates = dict(payload["symptom_updates"])
        last_answered = list(payload["answered_fields"])

        if has_sufficient_info(current_state):
            rag_ctx = retrieve_medlineplus_rag(current_state, verbose=verbose)
            analysis = generate_final_analysis(current_state, rag_ctx)
        else:
            analysis = payload["analysis"]

        final_payload = {
            "action": "COMPLETE",
            "symptom_updates": last_updates,
            "answered_fields": last_answered,
            "next_question": None,
            "analysis": analysis,
            "emergency_guidance": None,
        }
        validate_backend_response(final_payload)
        return final_payload

    validate_backend_response(payload)
    return payload


def process_turn_json(user_message, current_state, turn_count, max_turns=5):
    """백엔드 연동 시 호출할 함수: 설명이나 코드블록 없는 순수 JSON 문자열 반환"""
    payload = process_turn_for_backend(
        user_message=user_message,
        current_state=current_state,
        turn_count=turn_count,
        max_turns=max_turns,
        verbose=False,
    )
    return json.dumps(payload, ensure_ascii=False)


def main():
    demo_mode = "--demo" in sys.argv
    state = init_state()
    turn = 0
    max_turns = 5

    while turn < max_turns:
        try:
            user_input = input("환자: ")
        except EOFError:
            return
        if user_input.strip().lower() in ("exit", "quit"):
            return

        turn += 1
        backend_payload = process_turn_for_backend(
            user_message=user_input,
            current_state=state,
            turn_count=turn,
            max_turns=max_turns,
            verbose=demo_mode,
        )

        print(json.dumps(backend_payload, ensure_ascii=False, indent=2))

        if backend_payload["action"] == "EMERGENCY":
            return

        if backend_payload["action"] == "COMPLETE":
            if demo_mode:
                dept = (backend_payload.get("analysis") or {}).get("recommended_department")
                if dept:
                    region = state.get("region") or DEFAULT_REGION
                    hospitals = find_hospitals_from_nmc(region, dept)
                    print(f"\n[별도 API 조회 - 국립중앙의료원 병원 목록 (테스트 위치: {region} / 진료과: {dept})]")
                    if not hospitals:
                        print("- 조회된 병원이 없습니다.")
                    for idx, h in enumerate(hospitals, 1):
                        print(f"{idx}. 기관명: {h['기관명']} | 진료과목: {h['진료과목']}")
                print("\n※ 건강정보 출처: MedlinePlus.gov")
            return


if __name__ == "__main__":
    main()