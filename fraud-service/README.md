```markdown
# Bank Fraud Detection Rules & Scoring System

This document describes the **fraud‑detection rules** and a simple **risk‑scoring system (0–100)** used in the AI‑based fraud‑detection engine for this banking project.

---

## 📌 Data Columns Assumed

The rules below assume the following transaction columns (no device fingerprint):

- `Transaction Amount`  
- `Timestamp`  
- `Geo Location`  
- `IP Address`  
- `Merchant MCC`  
- `Customer RiskScore`  
- `Account CurrentBalance`  
- `Alert Status & Type`  
- `Client IBAN`  
- `counterparty IBAN`  
- `transaction type`

---

## 📋 Fraud Detection Rules

Each transaction is evaluated using the rules below. Every rule adds points to a **risk score** (0–100).

### 1. Large or unusual transaction amount
- Flag if:
  - `Transaction Amount` > 3,000 TND, **or**
  - `Transaction Amount` is a “round” number (e.g., 999, 1000, 1999, 5000, 9999) and the client is high‑risk.
- Why: large/round amounts are common in fund‑smuggling or card‑testing fraud.

### 2. High‑risk IBAN pattern
- Flag if:
  - `Client IBAN` is one of the known **suspicious IBANs** (e.g., `TN59 9000...7777`, `7778`, `7779`).
- Why: some accounts are flagged as structuring or money‑laundering‑prone.

### 3. Structured / layered transactions (structuring)
- Flag if:
  - Within the last **24 hours**, the same `Client IBAN` has:
    - 3 or more transactions, and  
    - each transaction amount is in the range **850–950 TND**.
- Why: this is classic AML “structuring” to avoid large‑transaction reporting thresholds.

### 4. Unusual hour or night transactions
- Flag if:
  - `transaction type` is `P2P_TRANSFER` or `INTERNATIONAL_TRANSFER`, and  
  - `Timestamp` is between **00:00–05:00** (night).
- Why: normal users rarely send large P2P or international transfers at night.

### 5. High‑risk IP / geography
- Flag if:
  - `IP Address` starts with `185.230` (foreign IP), **and**
  - either:
    - `Transaction Amount` > 2,000 TND, **or**
    - `Customer RiskScore` ≥ 70.
- Why: foreign‑IP transactions from high‑risk accounts are more likely to be fraudulent.

### 6. Unusual merchant / MCC
- Flag if:
  - `Merchant MCC` is in the high‑risk set: `5541`, `5999`, `5311`, **and**
  - `Transaction Amount` > 1,500 TND, **and**
  - there is no recent pattern of similar high‑value transactions for this MCC.
- Why: high‑value purchases in high‑risk MCCs are often fraud‑related.

### 7. Low‑balance vs high‑value transaction
- Flag if:
  - `Transaction Amount` > 0.8 × `Account CurrentBalance`.
- Why: moving most of the balance in one transaction is a high‑risk pattern.

### 8. Repeated alerts on the same IBAN
- Flag if:
  - In the last **7 days**, the same `Client IBAN` has:
    - 3 or more transactions where `Alert Status & Type` starts with `ALERTED`.
- Why: repeated alerts indicate an ongoing risk profile.

---

## 📊 Risk Scoring System (0–100)

Each applicable rule adds a fixed number of points. The total is capped at **100**.

| Rule condition                                                                 | Score points |
|--------------------------------------------------------------------------------|--------------|
| Transaction Amount > 3,000 TND                                                  | +20          |
| Transaction Amount is round number (e.g., 999, 1000, 5000, 9999)              | +15          |
| Client IBAN is a **suspicious IBAN**                                          | +20          |
| Counterparty IBAN is a **suspicious IBAN**                                     | +15          |
| Structuring pattern (3+ ~850–950 TND txns in 24h by same IBAN)                | +25          |
| Night transaction (00:00–05:00)                                               | +10          |
| Foreign IP (`185.230.x.x`)                                                    | +15          |
| Foreign IP + Transaction Amount > 2,000 TND                                   | +10 (extra)  |
| Customer RiskScore ≥ 70                                                       | +20          |
| Merchant MCC in high‑risk set (`5541`, `5999`, `5311`) and Amount > 1,500 TND | +10          |
| Amount > 80% of Account CurrentBalance                                        | +10          |
| Same Client IBAN already had ≥ 3 alerts in last 7 days                        | +20          |

Then compute:

- **Total risk score** = sum of all points above for that transaction, capped at **100**.

Define thresholds:

- **Low risk**  : `total_score` < 30  
- **Medium risk**: `30 ≤ total_score < 60`  
- **High risk** : `total_score ≥ 60`  

---

## ✅ How the AI System Uses These Rules

- All transactions with **score ≥ 60** are marked as **high‑risk** and:
  - can be **flagged for blocking**, or  
  - pushed to **manual review / fraud‑ops**.
- All transactions with **30 ≤ score < 60** can be treated as **medium‑risk**:
  - sent to a **secondary ML model**, or  
  - logged for **enhanced monitoring**.
- All transactions with **score < 30** are treated as **low‑risk** and typically processed normally.

You can plug this rule‑based logic into:

- A **pre‑processing calculator** (e.g., `calculate_fraud_score(row)` in Python), or  
- A **configuration layer** for your fraud‑detection engine (e.g., rule engine or decision table).

```