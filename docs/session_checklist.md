# Session checklist: a GPU day or the deployed window

Do these in this order. Each step says what it costs and how to know it worked.

1. **NAT Gateway on.** In `infra/terraform/terraform.tfvars` set `nat_gateway = true`, then
   `terraform -chdir=infra/terraform apply`. About 2 minutes; about $1.20 a day from now until step 7.
2. **Start.** `infra/start_work.sh --keep-worker-and-db --hours N` (N = 1 to 4) when others are watching
   (demo, study), `--keep-worker` when working alone, or plain `infra/start_work.sh` to let uploads
   start workers by themselves. A GPU worker costs $1.86 an hour (about $2.24 on the fallback type).
   It refuses unless the alarms, the breaker and the NAT Gateway are in place.
3. **Sign-in check.** Open the site's address (`terraform output public_base_url`) and sign in once.
4. **Credit.** Give the test account credit if it has none: `python infra/grant_credit.py ...`.
5. **Work.** A new worker is ready about 6.5 minutes after it starts. Run one short warm-up job first.
6. **Stop.** `infra/stop_work.sh`. Until step 7 is done it prints **NOT CONFIRMED** because the
   NAT Gateway (and its Elastic IP) still exist. Read the lines: that must be the ONLY problem listed.
   Any line about a worker, a machine, Aurora or the dead-letter queue is real: fix it first.
7. **NAT Gateway off.** Set `nat_gateway = false` and apply. Terraform refuses while the worker group
   is allowed to start workers (then run step 6 first). Run `infra/stop_work.sh` once more: it should
   now print **ALL STOPPED**.

Run `terraform apply` for anything else only after `stop_work.sh` (a held session changes Aurora's settings by hand).

If a step fails, do not skip ahead: the scripts name the cause. Aurora pauses by itself about 5 minutes
after its last use; the hourly reaper wakes it briefly (about $4 a month).
