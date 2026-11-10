import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { currentUser, type MeResponse } from "../api/auth";
import { updateMyChannel } from "../api/channels";
import { useAuth } from "../auth/AuthContext";

export function ProfilePage() {
  const { user, ready } = useAuth();
  const [profile, setProfile] = useState<MeResponse | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    if (!ready || !user) {
      return;
    }
    let cancelled = false;
    currentUser()
      .then((me) => {
        if (cancelled) {
          return;
        }
        setProfile(me);
        setName(me.channel.name);
        setDescription(me.channel.description);
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof ApiError ? caught.message : "Could not load your profile.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [ready, user]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSaved("");
    if (name.trim().length < 1 || name.trim().length > 80 || description.length > 2000) {
      setError("Name must be 1 to 80 characters and description at most 2000.");
      return;
    }
    try {
      const channel = await updateMyChannel({ name: name.trim(), description });
      setProfile((current) => (current ? { ...current, channel } : current));
      setSaved("Channel updated.");
      setError("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not update the channel.");
    }
  }

  if (!ready) {
    return <p>Checking your session.</p>;
  }
  if (!user) {
    return (
      <p>
        <Link to="/login">Log in</Link> to see your profile.
      </p>
    );
  }

  return (
    <section className="mx-auto max-w-lg">
      <h1 className="text-2xl font-semibold">{profile?.channel.name ?? user.channel.name}</h1>
      <p className="mt-2 text-sm text-muted">
        {user.email} · {user.role}
      </p>
      <p className="mt-4">
        <Link to={`/channels/${user.channel.handle}`}>View public channel</Link>
      </p>
      <form className="mt-6 space-y-4" onSubmit={onSubmit}>
        <label className="block text-sm">
          Display name
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2"
          />
        </label>
        <label className="block text-sm">
          Description
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2"
            rows={4}
          />
        </label>
        {error ? (
          <p role="alert" className="text-sm text-accent">
            {error}
          </p>
        ) : null}
        {saved ? <p className="text-sm text-muted">{saved}</p> : null}
        <button type="submit" className="rounded-full bg-accent px-4 py-2 text-sm font-medium">
          Save channel
        </button>
      </form>
    </section>
  );
}
