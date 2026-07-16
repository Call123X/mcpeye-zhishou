const form = document.getElementById("login-form");
const errorNode = document.getElementById("login-error");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorNode.hidden = true;
  const formData = new FormData(form);
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: formData.get("username"),
      password: formData.get("password"),
    }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "登录失败" }));
    errorNode.textContent = error.detail || "登录失败";
    errorNode.hidden = false;
    return;
  }
  window.location.href = "/";
});
