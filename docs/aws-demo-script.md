# BoeIoT — AWS Demo Recording Script

Live demo of the real-time **hot path**: `Kinesis → Lambda → SNS → SQS`.
Deployed to AWS account `586794461394`, region `us-east-1`, Terraform workspace `aws-demo`.

> **Golden rule for recording:** paste **one line at a time**. Never paste a
> multi-line command — the terminal wraps it and breaks it. Every command below
> is a single line on purpose.

---

## A. PREP — do this BEFORE you hit record (off-camera)

**A1. Confirm you are in the right Terraform workspace** (so you don't touch LocalStack state):

```bash
cd "/home/rosvend/Documents/Universidad/7. Septimo semestre/Datta Office Strategy/Entregas/Proyecto final/BoeIoT/src/infrastructure" && terraform workspace show
```
Expected output: `aws-demo`

**A2. Purge old test messages** so the demo shows only your fresh alert.
(Purge takes up to 60 seconds to finish — run it, then wait a minute.)

```bash
aws sqs purge-queue --profile default --region us-east-1 --queue-url https://sqs.us-east-1.amazonaws.com/586794461394/dos-boeing-737-max-alerts
```

**A3. Optional — pretty-print check.** If `jq` is installed the alert prints nicely.
Test with: `jq --version`. If missing, the demo still works (raw JSON).

---

## B. RECORDING SCRIPT (~5 minutes)

### Step 1 — Intro (~30s)
> "This is BoeIoT, an aircraft predictive-maintenance platform. I'll show the
> real-time anomaly hot path running on real AWS: a telemetry record enters a
> Kinesis stream, a Lambda scores it, and any anomaly is pushed out as an alert
> through SNS into an SQS queue."

Show the architecture diagram in `README.md` / `docs/img/`.

### Step 2 — Show the deployed infrastructure (~45s)
> "These are the live resources, deployed with Terraform."

```bash
cd "/home/rosvend/Documents/Universidad/7. Septimo semestre/Datta Office Strategy/Entregas/Proyecto final/BoeIoT/src/infrastructure" && terraform output
```

```bash
aws kinesis describe-stream-summary --profile default --region us-east-1 --stream-name dos-boeing-737-max-telemetry-stream
```
> "The stream is `ACTIVE` with one shard."

### Step 3 — Send an anomalous telemetry frame (~45s)
> "I'll inject one engine telemetry frame. Oil temperature and the cylinder
> spreads are far above the safe thresholds — this should trigger an alert."

```bash
aws kinesis put-record --profile default --region us-east-1 --stream-name dos-boeing-737-max-telemetry-stream --partition-key flight-001 --cli-binary-format raw-in-base64-out --data '{"flight_id":"flight-001","E1_OilT":0.99,"E1_OilP":0.05,"cht_spread":0.55,"egt_spread":0.60}'
```
> "Kinesis accepted the record — it returns the shard ID and a sequence number."

### Step 4 — Show the Lambda processed it (~45s)
Wait ~10 seconds, then:

```bash
aws logs tail /aws/lambda/dos-boeing-737-max-anomaly-detector --profile default --region us-east-1 --since 5m --format short
```
> "The Lambda fired automatically. The log shows `Published alert` with four
> anomaly types and `Batch done — processed=1, alerted=1`."

### Step 5 — Show the alert landed in SQS (~45s)
> "The alert travelled Lambda → SNS → SQS. Here it is in the queue."

With `jq` (pretty):
```bash
aws sqs receive-message --profile default --region us-east-1 --queue-url https://sqs.us-east-1.amazonaws.com/586794461394/dos-boeing-737-max-alerts --max-number-of-messages 10 --wait-time-seconds 20 | jq -r '.Messages[0].Body | fromjson'
```

Without `jq` (raw):
```bash
aws sqs receive-message --profile default --region us-east-1 --queue-url https://sqs.us-east-1.amazonaws.com/586794461394/dos-boeing-737-max-alerts --max-number-of-messages 10 --wait-time-seconds 20
```
> "The message body has the original frame plus the four detected anomalies,
> each with the value observed and the threshold it crossed."

### Step 6 — (Optional) Contrast with a healthy frame (~30s)
> "A normal reading produces no alert."

```bash
aws kinesis put-record --profile default --region us-east-1 --stream-name dos-boeing-737-max-telemetry-stream --partition-key flight-002 --cli-binary-format raw-in-base64-out --data '{"flight_id":"flight-002","E1_OilT":0.50,"E1_OilP":0.50,"cht_spread":0.10,"egt_spread":0.10}'
```
Then re-run the `logs tail` command — it shows `processed=1, alerted=0` and no new SQS message.

### Step 7 — Close (~20s)
> "End to end: ingestion, real-time detection, and alerting — all on AWS,
> reproducible from Terraform."

---

## C. TEARDOWN — run immediately after you stop recording

```bash
cd "/home/rosvend/Documents/Universidad/7. Septimo semestre/Datta Office Strategy/Entregas/Proyecto final/BoeIoT/src/infrastructure" && terraform destroy -auto-approve
```
```bash
cd "/home/rosvend/Documents/Universidad/7. Septimo semestre/Datta Office Strategy/Entregas/Proyecto final/BoeIoT/src/infrastructure" && terraform workspace select default
```
```bash
git checkout "src/infrastructure/providers.tf"
```
> Keep the `main.tf` lambda-path fix — it is a real bug fix. Restore only `providers.tf`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `argument --queue-url: expected one argument` | Pasted a multi-line command | Paste each command as ONE line |
| `Could not decode record (JSONDecodeError)` in logs | A space/newline got into the JSON on paste | Re-type/paste the `--data` JSON cleanly, one line |
| `receive-message` returns an old message | Stale message from a previous run | Run the **A2 purge** step before recording |
| `receive-message` returns nothing | Lambda not done yet, or queue already drained | Re-send a record (Step 3), wait, retry |
| Records not processed | Wrong Terraform workspace | `terraform workspace show` must say `aws-demo` |
