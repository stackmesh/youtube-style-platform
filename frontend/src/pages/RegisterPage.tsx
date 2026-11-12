import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { registerAccount } from "../api/auth";
import { useAuth } from "../auth/AuthContext";

const handlePattern = /^[a-z0-9_]{3,30}$/;

export function RegisterPage() {
  const navigate = useNavigate();
  const { setUser } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [handle, setHandle] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const normalizedHandle = handle.trim().toLowerCase();
    if (!email.includes("@") || password.length < 10 || password.length > 128) {
      setError("Use a valid email and a password of 10 to 128 characters.");
      return;
    }
    if (!handlePattern.test(normalizedHandle) || name.trim().length < 1 || name.trim().length > 80) {
      setError("Handle must be 3 to 30 letters, digits, or underscores, and name is required.");
      return;
    }
    try {
      const user = await registerAccount({
        email: email.trim(),
        password,
        handle: normalizedHandle,
        name: name.trim(),
      });
      setUser(user);
      navigate(`/channels/${user.channel.handle}`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not create the account.");
    }
  }

  return (
    <section className="mx-auto max-w-md">
      <h1 className="text-2xl font-semibold">Create account</h1>
      <form className="mt-6 space-y-4" onSubmit={onSubmit} noValidate>
        <Field label="Email" value={email} onChange={setEmail} type="email" />
        <Field label="Password" value={password} onChange={setPassword} type="password" />
        <Field label="Handle" value={handle} onChange={setHandle} />
        <Field label="Display name" value={name} onChange={setName} />
        {error ? (
          <p role="alert" className="text-sm text-accent">
            {error}
          </p>
        ) : null}
        <button type="submit" className="rounded-full bg-accent px-4 py-2 text-sm font-medium">
          Create account
        </button>
      </form>
      <p className="mt-4 text-sm text-muted">
        Already registered? <Link to="/login">Log in</Link>
      </p>
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
}) {
  return (
    <label className="block text-sm">
      {label}
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2"
      />
    </label>
  );
}
