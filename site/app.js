"use strict";

const filter = document.querySelector("#filter");
const body = document.querySelector("#findings-body");
const count = document.querySelector("#filter-count");

function updateCount() {
  if (!filter || !body || !count) {
    return;
  }
  const rows = Array.from(body.querySelectorAll("tr")).filter((row) =>
    row.querySelector("th")
  );
  const query = filter.value.trim().toLowerCase();
  let visible = 0;
  for (const row of rows) {
    const cells = Array.from(row.querySelectorAll("th, td"));
    if (cells.length < 5) {
      continue;
    }
    const matches = !query || cells.some((cell) =>
      cell.textContent.toLowerCase().includes(query)
    );
    row.hidden = !matches;
    if (matches) {
      visible += 1;
    }
  }
  count.textContent = `${visible} of ${rows.length} sites shown`;
}

if (filter) {
  filter.addEventListener("input", updateCount);
}
