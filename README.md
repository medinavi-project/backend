# MediNavi 백엔드

증상 문진 → 추천 진료과 → 지금 문 연 병원 → 약봉투 복약 안내를 잇는 MediNavi의 API 서버입니다.

- **문진**: CLOVA Studio HCX-005 다중 턴 문진 + MedlinePlus FAISS RAG, 응급 규칙 감지
- **병원**: 국립중앙의료원 병·의원 데이터, 여러 진료과 한 번에 검색, 진료 중 먼저 정렬
- **복약**: CLOVA OCR → 식약처 약 이름 사전(42,725개) 대조 → 쉬운 설명 + CLOVA Voice 음성

실행, 환경변수, 데이터 갱신, 테스트, 구조는 [backend/README.md](backend/README.md)를, 배포 설정은 [deploy/README.md](deploy/README.md)를 보세요.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

Swagger: http://localhost:8000/docs (배포: https://medinavi.kro.kr/docs)
