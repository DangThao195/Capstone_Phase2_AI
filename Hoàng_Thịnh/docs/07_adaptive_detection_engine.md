# Adaptive Detection Engine

## Muc tieu

Muc tieu cua engine khong phai la dat metric dep nhat tren mot bo du lieu duy nhat.

Muc tieu dung hon la:

- doc duoc nhieu bo du lieu khac nhau
- giu duoc logic detect on dinh
- khong vo ngay khi schema, phan bo score, hoac pattern anomaly thay doi
- van giu false positive o muc kiem soat duoc

Noi ngan gon:

`Engine nay duoc thiet ke de adapt theo data, khong phai de hard-tune cho rieng mot dataset.`

## Vi sao can cach tiep can adaptive

Trong qua trinh test, nhom co nhieu bo du lieu:

- bo goc 3 thang
- `data_test_v1`
- `data_test_v2`

Van de la moi bo co:

- do dai thoi gian khac nhau
- label quality khac nhau
- distribution score khac nhau
- anomaly va benign overlap khac nhau

Neu dung mot threshold co dinh va mot logic co dinh cho tat ca, engine se gap 2 tinh huong:

1. Mot bo data dep thi metric rat cao, nhung bo data khac se rot manh.
2. Neu tune lai cho bo data kho, bo data de co the bi giam precision.

Vi vay, engine can mot lop `adaptive calibration`.

## Nguyen tac thiet ke

Engine hien tai di theo 5 nguyen tac:

1. `Cost backbone` la nen chinh
2. `Telemetry` chi la lop bo sung
3. `Threshold` khong nen co dinh cho moi dataset
4. `Rule fallback` can ton tai cho case khong co telemetry
5. `Data quality` phai duoc kiem tra truoc khi tin model

## Pipeline adaptive hien tai

```text
Cost Explorer + CUR + Metrics
        |
        v
Data Cleaning / Join / Quality Checks
        |
        v
Feature Engineering
        |
        v
Temporal Split
        |
        +--> Walk-forward CV (neu du lich su)
        |
        +--> Tail Calibration (neu data ngan, khong du CV)
        |
        v
XGBoost Scoring
        |
        v
Service-aware Threshold Override (neu train set cho phep)
        |
        v
Hybrid Rule Fallback
        |
        v
FP Suppressor
        |
        v
Event Grouping / Dedup / Cleanup
        |
        v
Final Anomaly Incidents
```

## Cac co che adapt theo data

### 1. Walk-forward CV

Neu dataset du dai, engine uu tien `walk-forward cross-validation`.

Muc dich:

- calibrate threshold theo thoi gian
- tranh random split
- giam nguy co optimistic bias

Bo goc 3 thang du lich su nen phan nay hoat dong tot.

## 2. Tail Calibration

Neu dataset ngan, khong du fold cho walk-forward CV, engine se khong quay ve tuning in-sample thuan tuy.

Thay vao do, no:

- cat mot phan cuoi cua train window
- dung phan dau de fit model
- dung phan cuoi de chon threshold

Muc dich:

- threshold van duoc chon tren du lieu "gan holdout" hon
- giam tinh chat hard-code cua threshold

Day la co che quan trong cho `data_test_v1` va `data_test_v2`.

## 3. Threshold floor theo variant du lieu

Engine van giu `variant threshold floor` de khong cho threshold xuong qua thap.

Ly do:

- neu threshold qua thap, so alert co the bung ra rat manh
- precision giam nhanh

Tuy nhien, voi dataset ngan, floor nay duoc dieu chinh de mem hon so voi bo goc.

## 4. Service-aware threshold override

Neu mot service co du so dong train va du anomaly/normal, engine co the hoc threshold rieng cho service do.

Vi du:

- `AmazonEC2` co the co profile score khac `AmazonRDS`
- `AmazonCloudWatch` co the co profile khac `AmazonS3`

Muc dich:

- tranh viec tat ca service dung chung mot nguong
- giam miss o service kho
- giam false positive o service de nham

## 5. Hybrid fallback

Khong phai luc nao metrics cung du.

Vi vay engine giu mot lop `hybrid rule fallback` cho cac case:

- `cost-only`
- `no-telemetry`
- `cold-start`
- debug spike / CloudWatch spike

Neu khong co lop nay, engine supervised se bi mu voi nhung case nhu `A6` hoac nhung resource chi co cost ma khong co telemetry.

## 6. Label consistency check

Engine bo sung them lop kiem tra consistency cua label so voi `CUR`.

Muc dich:

- phat hien truong hop label map sai `resource`
- map sai `service`
- map sai `account`

Phan nay rat quan trong khi test bo data moi, vi co truong hop miss public eval la do label sai, khong phai do detector hong.

## Vi sao engine khong the "toan dien" cho moi bo data voi mot metric duy nhat

Khong co mot setting nao vua:

- precision rat cao
- recall rat cao
- FPR rat thap
- va on dinh tren moi dataset

Ly do:

- dataset de va dataset kho co phan bo khac nhau
- anomaly co the giong benign hon
- do dai chuoi thoi gian co the khac nhau
- service mix co the khac nhau

Vi vay, `adaptive engine` la cach dung hon so voi `hard-tuned engine`.

## Trade-off thuc te

Khi chuyen tu tuning cho mot dataset sang adaptive cho nhieu dataset, thuong se xay ra:

- mot so bo du lieu "de" co the mat bot precision
- nhung bo du lieu "kho" se duoc cuu recall va event-level match tot hon

Day khong phai bug.

Day la chi phi hop ly de dat duoc tinh tong quat hoa.

## Cach danh gia engine adaptive

Khong nen chi hoi:

- `precision cua v1 co con 0.8 khong?`

Ma nen hoi:

- co doc duoc bo data moi ma khong chet khong?
- co giu duoc public anomaly chinh khong?
- co giu FPR trong nguong cho phep khong?
- co phat hien duoc khi label bi sai khong?
- co degrade-safe mode khi data xau khong?

## Ket luan

Adaptive Detection Engine la huong:

- khong optimize tuyet doi cho mot bo data duy nhat
- ma toi uu cho kha nang song duoc tren nhieu bo data

Trong project nay, adaptive engine duoc xem la:

`mot engine co backbone co dinh, nhung co co che tu dieu chinh threshold, validation va fallback theo chat luong va dac diem cua tung dataset.`
