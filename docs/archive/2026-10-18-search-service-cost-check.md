# Search service cost check (Task A0 gate)

Date: 2026-10-18. Question: does a scale-to-zero Cloud Run service kept warm by a 60 s uptime ping cost about $0 idle?

**Verdict: PASS.** gen's backend uses exactly this pattern and its idle / min-instance billing is about $0.001 over 14 calendar days.

## Source

- BigQuery `nomadkaraoke.detailed_usage_cost_export.gcp_billing_export_resource_v1_01AD76_6E022B_321BFF`, read with the `claude-readonly` ADC (Python BigQuery client).
- Window 2026-10-05 to 2026-10-18 = 14 calendar days (10-18 is partial). `service.description = 'Cloud Run'`.
- Net = cost + credits (free tier applied at billing-account level).
- The Cloud Run service is named `karaoke-backend` (resource name in billing; not `karaoke-gen-backend`).

## gen backend config (verified in code)

- `karaoke-gen/.github/workflows/ci.yml`: `gcloud run deploy karaoke-backend --cpu 2 --memory 2Gi --cpu-throttling --concurrency 32 --min-instances 0`. Min-instances went 2 → 1 (2026-09-26) → 0 (2026-10-01) to cut about $24/mo idle charge.
- `infrastructure/modules/monitoring.py`: Cloud Monitoring uptime check on `/api/health`, `period=60s`. Its HISTORY comment still says "runs on --min-instances 1"; that comment is stale (ci.yml is authoritative).
- Request-based billing (`--cpu-throttling`): a warm-but-idle instance is not billed.

## karaoke-backend, 2026-10-05 to 10-18 (14 calendar days), by SKU

| SKU | Usage | Gross $ | Net $ |
|---|---|---|---|
| Services CPU (request-based) | 107,083 vCPU-s | 2.5697 | 2.3909 |
| Services Memory (request-based) | 98,527 GiB-s | 0.2460 | 0.2460 |
| Network egress (carrier peering NA) | 6.17 GB | 0.1143 | 0.1143 |
| Network egress (internet NA to NA) | 12 MB | 0.0011 | 0.0011 |
| **Services Min Instance CPU** | 246 vCPU-s | **0.0006** | 0.0006 |
| **Services Min Instance Memory** | 246 GiB-s | **0.0006** | 0.0006 |
| Requests | 219,995 | 0.0000 | 0.0000 |
| Other egress / intra-region | tiny | 0.0000 | 0.0000 |
| **Total** | | **2.9324** | **2.7536** |

Idle / min-instance SKUs total **$0.0012 over 14 calendar days** (appears on only 4 days: 10-06, 10-15, 10-16, 10-17; guess: brief revision overlap during deploys, not verified). There is no continuous idle charge. Cost sits on request-time SKUs.

Evidence the instance is not billed continuously while pinged: average request-time CPU is about 8.2k vCPU-s/day (a 2-vCPU instance running 24 h would be 172,800); the quietest hours bill about 50 vCPU-s. Cloud Run request_count over the last 24 h: about 6.0k requests (5,472 HTTP 200, 520 404s, rest negligible); billing averages about 15.7k/day over the window.

Daily gross $ (net equal except 10-05 net 0.0107 and 10-06 net 0.1266): 10-05 0.106, 10-06 0.210, 10-07 0.251, 10-08 0.178, 10-09 0.236, 10-10 0.135, 10-11 0.140, 10-12 0.230, 10-13 0.219, 10-14 0.125, 10-15 0.288, 10-16 0.401, 10-17 0.304, 10-18 0.110 (partial).

## karaoke-decide (current), same window

| SKU | Usage | Gross $ | Net $ |
|---|---|---|---|
| Services CPU (request-based) | 8,832 vCPU-s | 0.2119 | 0.1988 |
| Services Memory (request-based) | 5,055 GiB-s | 0.0126 | 0.0126 |
| Egress (all) | about 38 MB | 0.0008 | 0.0008 |
| Requests | 1,415 | 0 | 0 |
| **Total** | | **0.2253** | **0.2121** |

No min-instance SKUs. Daily gross $: 10-06 0.019, 10-07 0.019, 10-08 0.012, 10-09 0.023, 10-10 0.007, 10-11 0.013, 10-12 0.020, 10-13 0.010, 10-14 0.015, 10-15 0.018, 10-16 0.029, 10-17 0.040 (about $0.02/day, about $0.6/mo).

Other Cloud Run resources (context, gross): video-encoding-job 17.77, audio-separation-job 9.36, lyrics-transcription-job 0.76, mb-refresh 0.61, nomad-error-monitor 0.41, lb-refresh 0.27.

## Prices used

Derived directly from the billing rows above (agree with public Cloud Run us-central1 tier-1 list prices):

- Services, request-based: CPU $0.000024 per vCPU-s (2.5697 / 107,083); memory $0.0000025 per GiB-s; requests $0.40 per million after 2 M free/month.
- Jobs: CPU $0.000018 per vCPU-s (observed $1.79999e-5); memory $0.000002 per GiB-s (observed 1.8625e-15 per byte-s).
- Monthly free tier (180k vCPU-s, 360k GiB-s, 2 M requests) is largely consumed by gen + decide on the CPU/memory side (about 270k vCPU-s/month; their requests total only ~480k/month, well under the 2 M free requests), so new CPU/memory usage is costed at full price below (conservative).

## Ping volume (corrected after review)

- gen's uptime check (`monitoring.py`, `UptimeCheckConfig`, `period="60s"`) sets no `selected_regions`, so it runs from **all 6 checker locations**. Confirmed read-only via the Monitoring API: the live config has `selected_regions` empty and `check_passed` series exist for usa-virginia, usa-oregon, usa-iowa, eur-belgium, apac-singapore, sa-brazil-sao_paulo.
- Multiplier: **6 locations x 1,440 periods/day = 8,640 probes/day** (259,200/month), not 1,440. The 3-location minimum would be 4,320/day (129,600/month).
- Caveat: the raw `check_passed` sample count is about 8.6k per location per day (about 6x the configured rate; I could not explain the extra factor from the API), which I do not rely on. Cloud Run `request_count` for `karaoke-backend` shows only about 5.5k HTTP 200 responses in the last 24 h, including real traffic. So at most that many probes reach the origin; the 8,640 figure is a conservative upper bound (probes go through Cloudflare, which may absorb some).

## Estimate: new search service (1 vCPU, 4 GiB, request-based, min 0, pinged every 60 s)

Per month (30 days). Searches 5,000 x 0.2 s = 1,000 s; cold starts 1/day x 30 s x 30 = 900 s; pings = probes x 10 ms (nominal) or x 100 ms (pessimistic, billing rounds up to 100 ms). The daily cold start is the index-reload revision (the daily rebuild publishes a new index, which restarts the service).

| Scenario | Probes/month | Ping s | Total billed s (1 vCPU, 4 GiB) | CPU $ | Memory $ | Requests $ (if charged, $0.40/M) | Service total |
|---|---|---|---|---|---|---|---|
| Nominal, 6 regions (default) | 259,200 | 2,592 | 4,492 | 0.108 | 0.045 | 0.106 (likely free) | **$0.15 (to $0.26)** |
| Pessimistic, 6 regions | 259,200 | 25,920 | 27,820 | 0.668 | 0.278 | 0.106 | **$0.95 (to $1.06 incl. requests)** |
| Nominal, 3 regions | 129,600 | 1,296 | 3,196 | 0.077 | 0.032 | 0.054 | **$0.11 (to $0.16)** |
| Pessimistic, 3 regions | 129,600 | 12,960 | 14,860 | 0.357 | 0.149 | 0.054 | **$0.51 (to $0.56)** |

Request counts: 5,000 searches + probes (below the 2 M free requests/month, so likely $0). Idle / min-instance: $0.

Sensitivity (6 regions, nominal ping cost): 1 vCPU-s per search (5,000 s) and 10 cold starts/day (9,000 s) gives 16,592 billed s: CPU $0.398 + memory $0.166 = **$0.56/mo**, or about $1.50 with the build job.

**Recommendation:** set `selected_regions` to the 3-region minimum on the new check to halve probe volume.

## Estimate: daily index build job (2 vCPU, 8 GiB, about 600 s/day)

- vCPU-s = 2 x 600 x 30 = 36,000 x 0.000018 = $0.648.
- GiB-s = 8 x 600 x 30 = 144,000 x 0.000002 = $0.288.
- **About $0.94/mo.** Scales linearly: each extra 100 s/day of build adds about $0.16/mo.

## Total vs target

Build job $0.94 + service:

- Nominal, 6 regions: about $1.09 (up to $1.20 if requests are charged).
- Pessimistic, 6 regions: about $1.89.
- Nominal, 3 regions: about $1.05 (up to $1.10).
- Pessimistic, 3 regions: about $1.45 (up to $1.50).
- Sensitivity case: about $1.50.

Plus negligible GCS storage/egress for the index file. Target is at most $2/mo: **within budget, but the pessimistic 6-region case ($1.89) leaves little headroom**, so use 3 regions.

## Caveats

- Billing cannot prove warmth directly; it shows that idle time is unbilled and request-time dominated. First request after a long idle gap still pays a cold start (gen accepts about 15 s; the search service has a 4 GiB index to load, so expect longer, hence the 30 s/day estimate, and the daily index-reload revision forces one cold start per day).
- Concurrent requests share an instance and are billed once per instance-second, so the estimate is an upper bound for bursts.
- The build job could exceed 600 s; the $/100 s figure above covers that.
