# NCP GHCR CD 설정

대상 저장소: medinavi-project/backend. 대상 서버: **101.79.27.212** (`https://medinavi.kro.kr`).

main 대상 PR이 merge될 때만 자동 실행합니다. 닫기만 한 PR이나 main 직접 push는 배포하지 않습니다. 수동 실행은 Actions의 main 브랜치 workflow_dispatch를 사용합니다. main에 이 워크플로 파일이 먼저 반영돼야 합니다.

흐름: pytest/Ruff → Docker 이미지 linux/amd64 빌드 → GHCR push → SSH 전송 → digest로 pull → Docker Compose 재생성 → /health 검사. 새 배포 실패 시 이전 이미지·.env 복원 시도. 시연 서버 하나를 사용하며 hot reload와 소스 mount는 없습니다. 재생성 중 짧은 중단이 있습니다. /health는 Redis/API 상태만 확인하며 AI/OCR의 실제 인증·결과까지 확인하지 않습니다.

## GitHub Secrets

GitHub 저장소 → Settings → Secrets and variables → Actions → New repository secret.

| 이름 | 넣을 값 |
| --- | --- |
| NCP_SSH_USER | NCP 서버 SSH 로그인 계정 (예: ubuntu, root 등 실제 계정) |
| NCP_SSH_PRIVATE_KEY | 서버 authorized_keys의 공개키에 대응하는 전용 SSH 개인키 전체. 자동화용 비암호화 키 사용 |
| NCP_SSH_PORT | SSH 포트. 기본 22이므로 생략 가능 |
| NCP_SSH_KNOWN_HOSTS | 서버 SSH 호스트 공개키의 known_hosts 행. 서버 콘솔에서 fingerprint를 확인한 뒤 등록 |
| BACKEND_ENV | backend/.env 전체 내용. 실제 CLOVA OCR·식약처·CLOVA Studio(약 설명 CLOVA_API_KEY, 문진 CLOVASTUDIO_API_KEY)·공공데이터포털·네이버 지도·CLOVA Voice 키 포함. `MAX_CHAT_TURNS=7`, `CORS_ORIGINS`(프론트 주소), `ACME_EMAIL`(인증서 발급 알림 메일, 필수). 여러 줄 그대로 붙여넣기 |

이미지는 `ghcr.io/medinavi-project/backend`에 업로드합니다. 별도 GHCR 저장소를 미리 생성하거나 DOCKERHUB_USERNAME/DOCKERHUB_TOKEN을 등록할 필요가 없습니다. Actions가 제공하는 GITHUB_TOKEN에 packages:write 권한을 부여해 업로드하고, 배포 작업 중 같은 임시 토큰으로 NCP에서 ghcr.io에 로그인해 private 이미지도 pull합니다. 토큰은 작업 완료 후 만료되고 서버의 임시 Docker 인증 파일은 삭제됩니다. 패키지 공개 설정은 필수가 아닙니다. 조직 정책에서 Actions의 패키지 생성·접근을 허용해야 합니다. 기존에 같은 패키지가 있으면 이 저장소에 Actions 접근 권한을 부여해야 할 수 있습니다.

BACKEND_ENV는 backend/.env.example을 기반으로 실제 키·모델을 입력합니다. 현재 지원되는 모델과 실제 CORS 프론트 주소를 사용하세요. 실제 로컬 .env 파일은 Git에 올리지 않습니다. Docker 내부 REDIS_URL은 compose에서 redis://redis:6379/0으로 덮어씁니다. APP_ENV는 시연 설정인 development를 사용해도 됩니다. AI_MOCK_ENABLED=false를 권장합니다. 키 변경도 BACKEND_ENV를 갱신한 후 수동 배포하면 반영됩니다.

known_hosts를 수집하는 예시 (신뢰성 확인은 NCP 콘솔에서 별도 수행):

```bash
ssh-keyscan -p 22 -H 101.79.27.212
```

서버 콘솔에서 실제 fingerprint 확인:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

## NCP 서버 최초 준비

1. Ubuntu x86_64 서버와 공인 IP 연결을 확인합니다. 현재 이미지는 linux/amd64입니다. ARM 서버면 workflow platforms를 변경해야 합니다.
2. 서버에 Docker Engine과 Compose v2 plugin을 설치합니다. 공식 설치 안내: https://docs.docker.com/engine/install/ubuntu/
3. 배포 계정이 sudo 입력 없이 docker 명령을 실행할 수 있도록 권한을 설정합니다. docker 그룹 권한은 서버 관리자 수준 접근을 부여합니다.

```bash
sudo usermod -aG docker <배포계정>
# 로그아웃 후 재로그인
docker version
docker compose version
command -v flock
mkdir -p ~/medinavi
chmod 700 ~/medinavi
```

4. 배포 전용 SSH 공개키를 배포 계정 ~/.ssh/authorized_keys에 등록합니다. 디렉터리 700, 파일 600 권한을 적용합니다. 개인키는 GitHub Secret에만 등록합니다.
5. NCP ACG와 서버 방화벽에서 TCP 80·443(Caddy), 실제 SSH 포트를 허용합니다. 80은 인증서 발급 확인에 필요합니다. 8000은 Caddy를 거치게 되면 닫아도 됩니다. 서버에서 80/443을 쓰는 다른 프로그램(NGINX 등)이 있으면 꺼야 합니다. Redis 6379는 외부에 열지 않습니다. GitHub-hosted runner는 IP가 가변이므로 SSH 접근 정책을 이에 맞춰 설정해야 합니다.
6. 내도메인.한국에서 `medinavi.kro.kr`의 IP 연결(A 레코드)을 `101.79.27.212`로 설정합니다.
7. 위 SSH/BACKEND_ENV Secrets를 등록합니다. NCP 서버의 ghcr.io·이미지 다운로드 엔드포인트·공공 API·AI API로의 outbound HTTPS도 허용돼야 합니다.
8. 이 변경을 PR로 main에 merge합니다. Actions에서 테스트·빌드·배포 완료를 확인합니다.

NCP 자체 API 키는 SSH 배포에 사용하지 않으므로 필요 없습니다.

## 접속 및 확인

- Swagger: https://medinavi.kro.kr/docs
- Health: https://medinavi.kro.kr/health

## HTTPS (Caddy)

`deploy/Caddyfile`이 `medinavi.kro.kr`의 80/443을 받아 `api:8000`으로 넘깁니다. 인증서는 Caddy가 자동으로 받고 갱신하며 `caddy_data` 볼륨에 보관합니다. kro.kr은 Let's Encrypt 발급 한도를 다른 사용자와 같이 쓰므로 ZeroSSL을 먼저 시도하고 Let's Encrypt로 넘어갑니다. 인증서 문제는 `docker compose -f compose.yml logs caddy`로 확인합니다.

```bash
# 서버 배포 계정으로 로그인한 뒤
cd ~/medinavi
export API_IMAGE=$(cat .current-image)
docker compose -f compose.yml ps
docker compose -f compose.yml logs --tail 100 api
```

서버 ~/medinavi/.env는 CD가 BACKEND_ENV로 관리합니다. 서버에서 수동으로 바꾸면 다음 배포 때 덮어씁니다. 이미지는 commit 태그로 업로드하고 서버는 sha256 digest로 실행합니다. Redis에는 volume을 추가하지 않았고 저장은 비활성화되어 Redis 재시작 시 문진 세션이 사라집니다. API 배포만으로 Redis를 강제 재시작하지 않습니다.

처음 배포는 이전 버전이 없어 자동 복구할 대상이 없습니다. 이후 실패 시 이전 이미지/.env로 복구를 시도하고 Actions는 실패로 표시합니다. 이미지·컨테이너 자동 prune은 하지 않습니다.

## 검증 범위

로컬에서는 workflow YAML과 shell 구문 및 Compose 구조를 확인합니다. GitHub Secrets 등록, SSH 인증, NCP 포트 개방과 실제 최초 배포는 운영자 설정 후 확인해야 합니다. 이 파일 작성만으로 NCP 서버에 배포된 것은 아닙니다.

참고: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
Docker Actions: https://docs.docker.com/build/ci/github-actions/
GHCR: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry


수동으로 새 private 이미지를 pull할 때는 작업이 끝난 GITHUB_TOKEN을 재사용할 수 없습니다. 그 경우 별도 read:packages 권한 토큰으로 ghcr.io에 로그인하거나 Actions 수동 배포를 사용하세요. 일반적인 컨테이너 재시작에는 이미 다운로드된 이미지를 사용합니다.
