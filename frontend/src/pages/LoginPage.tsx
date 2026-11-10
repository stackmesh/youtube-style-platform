import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { loginAccount } from "../api/auth";
import { useAuth } from "../auth/AuthContext";

export function LoginPage() {
  const navigate = useNavigate();
  const { setUser } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!email.includes("@") || password.length < 10) {
      setError("Enter your email and a password of at least 10 characters.");
      return;
    }
    try {
      const user = await loginAccount(email.trim(), password);
      setUser(user);
      navigate("/profile");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not log in.");
    }
  }

  return (
    <section className="mx-auto max-w-md">
      <h1 className="text-2xl font-semibold">Log in</h1>
      <form className="mt-6 space-y-4" onSubmit={onSubmit} noValidate>
        <label className="block text-sm">
          Email
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2"
          />
        </label>
        <label className="block text-sm">
          Password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2"
          />
        </label>
        {error ? (
          <p role="alert" className="text-sm text-accent">
            {error}
          </p>
        ) : null}
        <button type="submit" className="rounded-full bg-accent px-4 py-2 text-sm font-medium">
          Log in
        </button>
      </form>
      <p className="mt-4 text-sm text-muted">
        New here? <Link to="/register">Create an account</Link>
      </p>
    </section>
  );
}
