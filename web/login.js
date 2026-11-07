"use strict";

const $ = (id) => document.getElementById(id);

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function showError(message) {
  const el = $("login-error");
  el.textContent = message;
  el.hidden = !message;
}

function showForm(which) {
  const signup = which === "signup";
  $("signin-form").hidden = signup;
  $("signup-form").hidden = !signup;
  $("show-signin").setAttribute("aria-pressed", String(!signup));
  $("show-signup").setAttribute("aria-pressed", String(signup));
  showError("");
}

// Asking for /api/auth/me is what makes the server set the CSRF cookie. It answers 401 when nobody is
// signed in, which is expected here. If someone already is, there is nothing to do on this page.
async function prepare() {
  try {
    const res = await fetch("/api/auth/me");
    if (res.ok) location.href = "/";
  } catch (err) {
    showError("Could not reach the server.");
  }
}

async function submit(form, path, fields) {
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  showError("");
  try {
    await ready;
    const body = {};
    for (const [key, id] of Object.entries(fields)) body[key] = $(id).value;
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError(data.error || `Request failed (${res.status}).`);
      return;
    }
    location.href = "/";
  } catch (err) {
    showError("Could not reach the server.");
  } finally {
    button.disabled = false;
  }
}

const ready = prepare();

$("show-signin").addEventListener("click", () => showForm("signin"));
$("show-signup").addEventListener("click", () => showForm("signup"));
$("signin-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submit(event.target, "/api/auth/login", { handle: "signin-handle", password: "signin-password" });
});
$("signup-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submit(event.target, "/api/auth/signup", {
    handle: "signup-handle",
    password: "signup-password",
    invite_code: "signup-invite",
  });
});
