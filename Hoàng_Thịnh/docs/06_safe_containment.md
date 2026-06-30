# Safe Containment

## Safe Containment la gi

Safe Containment la buoc phan ung an toan sau khi engine phat hien anomaly.

Muc tieu cua no la:

- giam thiet hai neu anomaly la that
- tranh tac dong nguy hiem neu model du doan sai
- dam bao moi hanh dong deu co the review, rollback, hoac chay o che do an toan

Noi ngan gon:

`Detect` tra loi cau hoi: "co bat thuong hay khong?"

`Safe Containment` tra loi cau hoi: "neu bat thuong co kha nang la that, he thong nen phan ung the nao de an toan nhat?"

## Vi sao can Safe Containment

Trong bai toan FinOps, neu chi detect ma khong co lop containment thi se gap 2 van de:

1. Neu anomaly la that, chi phi co the tiep tuc tang trong luc cho con nguoi xu ly.
2. Neu model bao nham ma he thong tu dong chay lenh nguy hiem, workload that co the bi anh huong.

Vi vay, engine khong nen nhay thang tu detect sang "tu dong stop hoac xoa tai nguyen".

No nen di qua mot lop trung gian an toan hon.

## Cach Safe Containment duoc dung trong engine nay

Engine hien tai su dung huong:

```text
Detect anomaly
-> danh gia confidence va context
-> chon hanh dong an toan nhat
-> tra ra safe action payload
-> cho review / extend / rollback
```

Nghia la engine chi de xuat hanh dong co kiem soat, khong tu dong sinh lenh AWS nguy hiem.

## Cac hanh dong Safe Containment hien co

Trong implementation hien tai, engine uu tien cac action sau:

- `tag-for-review`
  - danh dau resource de team review
  - day la hanh dong an toan nhat va duoc dung nhieu nhat

- `manual-review-only`
  - chi canh bao va yeu cau con nguoi xac nhan
  - dung khi data chua du chac hoac confidence thap

- `schedule-shutdown-review`
  - de xuat dua resource vao luong review truoc khi shutdown
  - khong phai "stop ngay lap tuc"

- `restrict-quota-request`
  - de xuat bo sung mot lop chan o muc quota/request
  - phu hop khi nghi ngo runaway usage hoac spend burst

## Nguyen tac an toan

Safe Containment trong engine nay duoc thiet ke theo 5 nguyen tac:

1. Khong destructive by default

- Khong xoa resource
- Khong terminate truc tiep
- Khong tu dong sinh AWS CLI nguy hiem de chay ngay

2. Confidence-aware

- Neu confidence thap, action se nhe hon
- Neu du lieu estimated hoac context chua ro, engine uu tien `manual-review-only`

3. Context-aware

- Neu anomaly co kha nang la:
  - migration
  - load test
  - campaign
  - benign growth
- thi engine co the suppress hoac downgrade action

4. Rollback-friendly

- Moi action duoc thiet ke de co the `extend` hoac `rollback`
- Day la ly do engine co them luong action state va rollback payload

5. Human-in-the-loop

- Engine de xuat
- Con nguoi phe duyet hoac xac nhan
- He thong khong tu y containment manh khi chua du bang chung

## Khi nao engine se chon containment nhe hon

Engine se uu tien containment nhe hon trong cac truong hop sau:

- `is_estimated = true`
- telemetry thieu hoac data quality kem
- anomaly moi nhung thieu baseline ro rang
- confidence thap
- co business context co the la benign

Khi do, output hop ly nhat thuong la:

- `manual-review-only`
- hoac `tag-for-review`

## Khi nao co the containment manh hon

Containment manh hon chi nen dung khi:

- confidence cao
- anomaly lap lai ro
- cost impact lon
- context phu hop voi runaway / severe spike
- co rollback path ro rang

Ngay ca khi do, engine hien tai van giu huong an toan:

- `schedule-shutdown-review`
- `restrict-quota-request`

chu khong "tu dong huy ngay".

## Vi du de hieu

### Vi du 1: Untagged spend

- Detect thay resource untagged ton tien lon
- Safe Containment phu hop:
  - `tag-for-review`
- Ly do:
  - van de la governance/cost ownership
  - khong can shutdown resource

### Vi du 2: Runaway usage o dev

- Detect thay resource moi trong `dev` tang chi phi nhanh
- Safe Containment phu hop:
  - `schedule-shutdown-review`
  - hoac `restrict-quota-request`
- Ly do:
  - can giam rui ro spend
  - nhung van phai giu kha nang rollback

### Vi du 3: Spike estimated data

- Detect thay spike nhung du lieu van la `estimated`
- Safe Containment phu hop:
  - `manual-review-only`
- Ly do:
  - chua du chac de containment manh

## Diem manh cua cach lam nay

- thuc dung hon so voi chi detect roi dung
- an toan hon so voi auto-remediation truc tiep
- hop voi bai toan demo/capstone va de defend voi mentor
- de nang cap sau nay thanh workflow that voi allowlist va approval

## Han che hien tai

- chua co workflow phe duyet thuc su voi IAM/Slack/Jira
- state hien tai chua phai persistence production
- chua co action executor that tren AWS
- van la `safe payload`, chua phai auto-remediation production

## Cach mo rong sau nay

Neu muon dua len muc production-like hon, co the mo rong theo huong:

- them approval workflow
- them allowlist cho action
- them audit trail ben vung
- them persistence cho action state
- them executor rieng de map payload sang AWS API

## Ket luan

Safe Containment la lop giua `detect` va `action`.

No giup he thong:

- khong dung lai o muc canh bao
- nhung cung khong di qua gioi han an toan

Voi engine hien tai, Safe Containment co the xem la:

`de xuat hanh dong an toan, co the review, co the rollback, va khong destructive by default.`
