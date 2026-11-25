# 구현 현황

초기(Gemini 기반) 구현 보고는 git 기록에 남아 있습니다. 지금 구조·실행·환경변수·테스트는 [README.md](README.md)를, 문진 LLM 규격은 [docs/llm-contract.md](docs/llm-contract.md)를 보세요.

| 기능 | 구현 |
| --- | --- |
| 문진 | CLOVA Studio HCX-005 다중 턴(최대 7번), 서버가 종료·추가 질문 결정, 응급은 키워드 규칙 |
| RAG | MedlinePlus 1,017건, `bge-m3` 임베딩 + FAISS `IndexFlatIP`, 상위 3건. 인덱스 없으면 실시간 검색 |
| 병원 | 국립중앙의료원 API, 진료과 최대 5개 동시 검색, 진료 중 먼저 정렬, 1시간 캐시, 한도 초과 시 429 |
| 복약 | CLOVA OCR → 약 이름 사전 42,725개 유사도 매칭 → 쉬운 설명(HTML 제거) |
| 음성 | CLOVA Voice Premium(`nara`), 재생 시 생성·캐시 |
| 저장소 | Redis 세션(TTL 30분)만 사용, DB 없음 |
