# 브롤 드래프트 랩

Brawl Stars 랭크전 데이터를 모아 **맵별로 픽·밴을 추천**하는 개인용 비공식 팬 사이트입니다.
PC나 서버 없이 **GitHub(무료)만으로 운영**되고, 태블릿 브라우저에서 바로 씁니다.

```
GitHub Actions (1시간마다 자동 실행)
  ① collector/collect.py   공식 API(프록시 경유)로 랭크전 결과 수집 → data 브랜치에 저장
  ② collector/aggregate.py 맵별 통계·모델 계산 → site/data/stats.json
  ③ GitHub Pages           site/ 폴더를 웹사이트로 배포
태블릿 브라우저 → https://<아이디>.github.io/<저장소이름>/
```

## 처음 설정 (모두 태블릿 브라우저에서 가능)

### 1. 사이트 먼저 띄우기 (샘플 데이터)
1. 이 저장소의 **Settings → Pages**에서 *Build and deployment*의 **Source**를 **GitHub Actions**로 바꿉니다.
2. **Actions** 탭 → 왼쪽 *데이터 수집 및 사이트 배포* → **Run workflow** → *샘플 데이터로 배포*에 체크 → 실행합니다.
3. 2~3분 뒤 `https://<아이디>.github.io/<저장소이름>/` 에 접속합니다. 노란 띠(가짜 샘플 데이터)가 보이면 성공입니다.

### 2. 실제 데이터 연결 (API 키)
공식 API 키는 등록한 IP에서만 작동합니다. GitHub의 서버는 IP가 매번 바뀌므로,
고정 IP를 가진 공개 프록시(RoyaleAPI의 `bsproxy.royaleapi.dev`)를 거쳐 요청합니다.

1. https://developer.brawlstars.com 에 가입하고 로그인합니다.
2. **My Account → Create New Key**에서 이름과 설명을 적고,
   **Allowed IP addresses**에 `45.79.218.79` (RoyaleAPI 프록시 IP)를 넣어 키를 만듭니다.
3. 만든 키의 긴 문자열(토큰)을 복사합니다. **이 값은 채팅이나 코드에 붙여 넣지 마세요.**
4. 이 저장소의 **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `BS_API_TOKEN`
   - Secret: 복사한 토큰
5. **Actions** 탭에서 *Run workflow*를 체크 없이 실행합니다. 이후로는 1시간마다 자동 실행됩니다.
   처음 실제 수집 때 샘플 데이터는 자동으로 지워집니다.

> 맵마다 쓸 만한 통계가 나오려면 데이터가 쌓여야 합니다. 맵당 200판이 넘으면 사이트에 나타나고,
> 브롤러 강도는 맵당 수만 판, 시너지·카운터는 그보다 훨씬 많은 판이 필요합니다.

### 문제 해결
- **Actions가 빨간 X로 실패하고 로그에 403**: 키의 IP가 `45.79.218.79`인지, Secret 이름이 정확히 `BS_API_TOKEN`인지 확인합니다.
- **사이트가 안 바뀜**: Settings → Pages의 Source가 *GitHub Actions*인지 확인합니다.
- **자동 실행이 멈춤**: 공개 저장소는 오래 활동이 없으면 예약 실행이 꺼질 수 있습니다. Actions 탭에서 *Enable workflow*를 누릅니다.

## 추천 방식 요약
- **모델 (맵마다 따로)**: `logit P(우리 승) = Σ강도(우리) − Σ강도(상대) + Σ시너지(우리 쌍) − Σ시너지(상대 쌍) + Σ카운터(우리 vs 상대)`
- **강도**: 릿지(L2) 로지스틱 회귀. 판수가 적은 브롤러의 극단값을 0 쪽으로 줄입니다.
- **시너지·카운터**: 강도 모델의 잔차로 쌍별 추정(정규화 뉴턴 1단계). 점수검정 |z| ≥ 3인 쌍만 사용합니다.
- **탐색**: 남은 픽을 끝까지 보는 미니맥스 + 알파-베타 + 빔(단계별 후보 5~12명).

검증(가짜 데이터, 맵 10개 × 4만 판, 정답 파라미터를 알고 있는 상태):

| 항목 | 결과 |
|---|---|
| 브롤러 강도 추정 vs 정답 상관 | 0.92 ~ 0.97 (단순 승률은 0.87 ~ 0.94) |
| 시너지·카운터 기준 \|z\| | 2.0 / 2.5 / **3.0** / 3.5 / 4.0 중 3.0에서 검증 데이터 로그손실 최소 |
| 검출된 진짜 시너지·카운터의 부호 일치 | 35 / 35 |

## 로컬에서 실행 (선택)
```bash
pip install -r collector/requirements.txt
STORE_DIR=store python collector/make_demo.py      # 샘플 데이터
STORE_DIR=store python collector/aggregate.py      # → site/data/stats.json
python -m http.server -d site 8000                 # http://localhost:8000
```

## 주의
- 비공식 팬 콘텐츠입니다. Supercell이 승인·후원하지 않으며, [Supercell 팬 콘텐츠 정책](https://www.supercell.com/fan-content-policy)을 따릅니다.
- 플레이어 태그는 저장하지 않습니다(중복 판별용 해시에만 일방향으로 사용).
- RoyaleAPI 프록시는 Supercell 공식 서비스가 아니며, 계속 제공된다는 보장이 없습니다.
