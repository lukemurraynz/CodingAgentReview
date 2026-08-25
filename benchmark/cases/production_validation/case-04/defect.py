def record_job_done(metrics):
    # emits jobs_done_v2; dashboards read jobs_done (v1)
    metrics.increment('jobs_done_v2')
