# Session checklist: a GPU day or the deployed window

Do these in this order. Each step says what it costs and how to know it worked.

1. **Start.** `infra/start_work.sh --keep-warm --hours N` (N = 1 to 4) when others are
   watching (demo, study), `--keep-warm` when working alone, or plain `infra/start_work.sh` to let
   uploads start the GPU by themselves. A GPU costs $1.86 an hour (about $2.24 on the fallback
   type); it stops itself 7 idle minutes after the last job.
2. **Sign-in check.** Open the site's address (`terraform output public_base_url`) and sign in once.
3. **Credit.** Give the test account credit if it has none: `python infra/grant_credit.py ...`.
4. **Work.** An upload starts the endpoint; a new GPU can take about 10 minutes to be ready. Run one
   short warm-up job first.
5. **Stop.** `infra/stop_work.sh`. It ends any keep-warm, sets the endpoint's scaling maximum and
   minimum to 0 and checks both, waits for the GPU to go and puts Aurora's minimum back to 0. It
   prints **ALL STOPPED** only when every check succeeded; anything it could not prove is printed
   as **NOT CONFIRMED** with the reason.

Run `terraform apply` for anything else only after `stop_work.sh` (a held session changes Aurora's settings by hand).

If a step fails, do not skip ahead: the scripts name the cause. Aurora pauses by itself about 5 minutes
after its last use; the reaper wakes it every 3 hours (about $3 a month).

After a code change that adds a file to `infra/migrations/`, run `python infra/apply_schema.py --backend data_api` (with the `NEUROLENS_DB_*` values from `terraform output`) before the site is used; the deploy scripts do not apply migrations. It wakes Aurora for a few minutes.
