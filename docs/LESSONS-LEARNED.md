# Lessons Learned

Accumulated wisdom from building Nomad Karaoke Decide. Add entries as you learn things that future sessions should know.

## Format

```markdown
### YYYY-MM-DD: Brief Title
**Context:** What you were doing
**Lesson:** What you learned
**Recommendation:** What to do differently
```

---

## Entries

### 2024-12-30: BigQuery over Firestore for Large Catalogs

**Context:** Loading 275K karaoke songs and 256M Spotify tracks for search/browse.

**Lesson:** BigQuery is better than Firestore for large read-heavy datasets with complex queries. Firestore would require denormalized indexes and be expensive for full-text search across millions of records.

**Recommendation:** Use BigQuery for catalog data (songs, tracks). Reserve Firestore for user-specific data (preferences, playlists, history) where document-based access patterns make sense.

---

### 2024-12-30: GCE VM for Large ETL Jobs

**Context:** Needed to process 186GB Spotify torrent data (decompress .zst, query SQLite, export to BigQuery).

**Lesson:** Local machines with SD cards are too slow for large ETL. Spinning up a GCE VM with SSD in the same region as BigQuery is much faster (minutes vs hours).

**Recommendation:** For large data processing:
1. Create GCE VM near your data destination
2. Download/process data there
3. Delete VM when done to avoid costs

---

### 2024-12-30: Cloud Run Port Configuration

**Context:** Deploying FastAPI to Cloud Run, got "container failed to start" errors.

**Lesson:** Cloud Run defaults to PORT=8080. If your Dockerfile uses a different port (we used 8000), you must specify `--port=8000` in the deploy command.

**Recommendation:** Either:
- Update Dockerfile to use 8080
- Or always specify `--port` in `gcloud run deploy`

---

### 2024-12-30: Service Account Permissions for BigQuery

**Context:** Cloud Run service couldn't query BigQuery - got 403 "User does not have bigquery.jobs.create permission".

**Lesson:** Default compute service account needs explicit BigQuery roles.

**Recommendation:** Grant these roles to the Cloud Run service account:
```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/bigquery.user"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/bigquery.dataViewer"
```

---

### 2024-12-30: GitHub Pages Custom Domain Setup

**Context:** Deploying Next.js static site to decide.nomadkaraoke.com via GitHub Pages.

**Lesson:** GitHub Pages needs to be enabled AND configured for GitHub Actions deployment before the workflow will succeed.

**Recommendation:**
1. Enable Pages in repo settings OR via API: `gh api repos/OWNER/REPO/pages -X POST -f build_type="workflow"`
2. Set custom domain: `gh api repos/OWNER/REPO/pages -X PUT -f cname="your.domain.com"`
3. Create `public/CNAME` file with domain
4. Configure DNS CNAME to point to `username.github.io`

---

### 2024-12-30: Importing Existing GCP Resources into Pulumi

**Context:** Infrastructure was created via `gcloud` commands but CLAUDE.md requires all GCP changes via Pulumi. Needed to bring existing resources under IaC control.

**Lesson:** Pulumi import workflow is straightforward but requires matching IaC to exact API state to avoid drift.

**Recommendation:**
1. Write IaC definitions first (approximate is fine)
2. Import each resource: `pulumi import <type> <name> <gcp-id>`
3. Copy the suggested code from import output to match exact state
4. Run `pulumi preview` - iterate until "0 changes"
5. Use `protect=True` on stateful resources (BigQuery tables, buckets)

**Import ID formats:**
```
BigQuery Dataset: projects/{project}/datasets/{dataset}
BigQuery Table:   projects/{project}/datasets/{dataset}/tables/{table}
GCS Bucket:       {bucket-name}
Artifact Reg:     projects/{project}/locations/{region}/repositories/{repo}
Cloud Run:        projects/{project}/locations/{region}/services/{service}
IAM Member:       {project} {role} {member}
```
