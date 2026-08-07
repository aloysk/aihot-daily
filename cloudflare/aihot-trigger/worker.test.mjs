import assert from "node:assert/strict";
import test from "node:test";

import worker from "./worker.mjs";

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("primary cron requests workflow run details from GitHub", async (t) => {
  let request;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    request = { url, init };
    return new Response(
      JSON.stringify({
        workflow_run_id: 31128351443,
        run_url:
          "https://api.github.com/repos/aloysk/aihot-daily/actions/runs/31128351443",
        html_url:
          "https://github.com/aloysk/aihot-daily/actions/runs/31128351443",
      }),
      { status: 200 },
    );
  });
  t.mock.method(console, "log", () => {});

  await worker.scheduled(
    { cron: "30 21 * * *", scheduledTime: Date.parse("2026-08-08T21:30:00Z") },
    { GH_PAT: "test-token" },
  );

  assert.equal(
    request.url,
    "https://api.github.com/repos/aloysk/aihot-daily/actions/workflows/daily.yml/dispatches",
  );
  assert.deepEqual(JSON.parse(request.init.body), {
    ref: "master",
    return_run_details: true,
  });
  assert.equal(request.init.headers.Authorization, "Bearer test-token");
  assert.equal(request.init.headers["X-GitHub-Api-Version"], "2026-03-10");
});

test("monitor cron reruns only a completed zero-step runner acquisition failure", async (t) => {
  const requests = [];
  const responses = [
    jsonResponse({
      total_count: 1,
      workflow_runs: [
        {
          id: 31128351443,
          event: "workflow_dispatch",
          head_branch: "master",
          created_at: "2026-08-08T21:30:22Z",
          status: "completed",
          conclusion: "failure",
          run_attempt: 1,
        },
      ],
    }),
    jsonResponse({
      total_count: 1,
      jobs: [
        {
          id: 92708739333,
          status: "completed",
          conclusion: "cancelled",
          runner_id: 0,
          runner_name: "",
          steps: [],
        },
      ],
    }),
    new Response(null, { status: 201 }),
  ];
  t.mock.method(globalThis, "fetch", async (url, init = {}) => {
    requests.push({ url, method: init.method ?? "GET" });
    return responses.shift();
  });
  t.mock.method(console, "log", () => {});

  await worker.scheduled(
    { cron: "48 21 * * *", scheduledTime: Date.parse("2026-08-08T21:48:00Z") },
    { GH_PAT: "test-token" },
  );

  assert.deepEqual(requests, [
    {
      url:
        "https://api.github.com/repos/aloysk/aihot-daily/actions/workflows/daily.yml/runs?event=workflow_dispatch&branch=master&per_page=10",
      method: "GET",
    },
    {
      url:
        "https://api.github.com/repos/aloysk/aihot-daily/actions/runs/31128351443/jobs?per_page=100",
      method: "GET",
    },
    {
      url:
        "https://api.github.com/repos/aloysk/aihot-daily/actions/runs/31128351443/rerun",
      method: "POST",
    },
  ]);
});

test("monitor cron does not rerun a workflow that executed application steps", async (t) => {
  const requests = [];
  const responses = [
    jsonResponse({
      total_count: 1,
      workflow_runs: [
        {
          id: 30768056910,
          event: "workflow_dispatch",
          head_branch: "master",
          created_at: "2026-08-08T21:30:22Z",
          status: "completed",
          conclusion: "failure",
          run_attempt: 1,
        },
      ],
    }),
    jsonResponse({
      total_count: 1,
      jobs: [
        {
          id: 92341996528,
          status: "completed",
          conclusion: "failure",
          runner_id: 1000001536,
          runner_name: "GitHub Actions 1000001536",
          steps: [
            {
              name: "Generate & send daily weather + quote",
              status: "completed",
              conclusion: "failure",
              number: 4,
            },
          ],
        },
      ],
    }),
  ];
  t.mock.method(globalThis, "fetch", async (url, init = {}) => {
    requests.push({ url, method: init.method ?? "GET" });
    return responses.shift();
  });
  t.mock.method(console, "log", () => {});

  await worker.scheduled(
    { cron: "48 21 * * *", scheduledTime: Date.parse("2026-08-08T21:48:00Z") },
    { GH_PAT: "test-token" },
  );

  assert.equal(requests.length, 2);
  assert.equal(requests.some(({ url }) => url.endsWith("/rerun")), false);
});

test("monitor cron dispatches a fallback when the primary run is absent", async (t) => {
  const requests = [];
  const responses = [
    jsonResponse({ total_count: 0, workflow_runs: [] }),
    jsonResponse({
      workflow_run_id: 31200000000,
      run_url:
        "https://api.github.com/repos/aloysk/aihot-daily/actions/runs/31200000000",
      html_url:
        "https://github.com/aloysk/aihot-daily/actions/runs/31200000000",
    }),
  ];
  t.mock.method(globalThis, "fetch", async (url, init = {}) => {
    requests.push({ url, method: init.method ?? "GET", body: init.body });
    return responses.shift();
  });
  t.mock.method(console, "log", () => {});

  await worker.scheduled(
    { cron: "48 21 * * *", scheduledTime: Date.parse("2026-08-08T21:48:00Z") },
    { GH_PAT: "test-token" },
  );

  assert.equal(requests.length, 2);
  assert.equal(requests[1].method, "POST");
  assert.deepEqual(JSON.parse(requests[1].body), {
    ref: "master",
    return_run_details: true,
  });
});
