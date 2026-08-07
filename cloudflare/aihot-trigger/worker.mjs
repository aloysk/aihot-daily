const API_ROOT = "https://api.github.com/repos/aloysk/aihot-daily";
const WORKFLOW_PATH = "daily.yml";
const PRIMARY_CRON = "30 21 * * *";
const MONITOR_CRON = "48 21 * * *";
const MONITOR_DELAY_MS = 18 * 60 * 1000;
const PRIMARY_MATCH_WINDOW_MS = 10 * 60 * 1000;

function githubHeaders(env) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${env.GH_PAT}`,
    "Content-Type": "application/json",
    "User-Agent": "aihot-trigger",
    "X-GitHub-Api-Version": "2026-03-10",
  };
}

async function githubRequest(env, url, init = {}) {
  const response = await fetch(url, {
    ...init,
    headers: { ...githubHeaders(env), ...init.headers },
  });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(`GitHub API ${response.status}: ${detail}`);
  }
  const body = await response.text();
  return body ? JSON.parse(body) : null;
}

async function dispatchWorkflow(env) {
  const details = await githubRequest(
    env,
    `${API_ROOT}/actions/workflows/${WORKFLOW_PATH}/dispatches`,
    {
      method: "POST",
      body: JSON.stringify({ ref: "master", return_run_details: true }),
    },
  );
  if (!Number.isInteger(details?.workflow_run_id)) {
    throw new Error("GitHub dispatch response omitted workflow_run_id");
  }
  console.log(
    `dispatched workflow_run_id=${details.workflow_run_id} url=${details.html_url}`,
  );
  return details.workflow_run_id;
}

function findPrimaryRun(runs, scheduledTime) {
  const expectedPrimaryTime = scheduledTime - MONITOR_DELAY_MS;
  return (
    runs
      .filter(
        (run) =>
          run.event === "workflow_dispatch" &&
          run.head_branch === "master" &&
          Math.abs(Date.parse(run.created_at) - expectedPrimaryTime) <=
            PRIMARY_MATCH_WINDOW_MS,
      )
      .sort(
        (left, right) =>
          Math.abs(Date.parse(left.created_at) - expectedPrimaryTime) -
          Math.abs(Date.parse(right.created_at) - expectedPrimaryTime),
      )[0] ?? null
  );
}

function isRunnerAcquisitionFailure(jobs) {
  return (
    jobs.length > 0 &&
    jobs.every(
      (job) =>
        job.status === "completed" &&
        job.conclusion === "cancelled" &&
        job.runner_id === 0 &&
        Array.isArray(job.steps) &&
        job.steps.length === 0,
    )
  );
}

async function monitorPrimaryRun(env, scheduledTime) {
  const data = await githubRequest(
    env,
    `${API_ROOT}/actions/workflows/${WORKFLOW_PATH}/runs?event=workflow_dispatch&branch=master&per_page=10`,
  );
  const run = findPrimaryRun(data.workflow_runs ?? [], scheduledTime);
  if (!run) {
    console.log("primary run not found; dispatching fallback");
    await dispatchWorkflow(env);
    return;
  }
  if (run.status !== "completed" || run.conclusion !== "failure") {
    console.log(`primary run ${run.id} status=${run.status}/${run.conclusion}`);
    return;
  }

  const jobsData = await githubRequest(
    env,
    `${API_ROOT}/actions/runs/${run.id}/jobs?per_page=100`,
  );
  if (!isRunnerAcquisitionFailure(jobsData.jobs ?? [])) {
    console.log(`primary run ${run.id} failed after execution; not rerunning`);
    return;
  }

  await githubRequest(env, `${API_ROOT}/actions/runs/${run.id}/rerun`, {
    method: "POST",
  });
  console.log(`rerun requested for zero-step runner failure ${run.id}`);
}

export default {
  async scheduled(event, env) {
    if (event.cron === PRIMARY_CRON) {
      await dispatchWorkflow(env);
      return;
    }
    if (event.cron === MONITOR_CRON) {
      await monitorPrimaryRun(env, event.scheduledTime);
      return;
    }
    throw new Error(`Unexpected cron expression: ${event.cron}`);
  },
};
