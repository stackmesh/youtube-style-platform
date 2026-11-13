import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  channelPlaylists,
  createPlaylist,
  deletePlaylist,
  getPlaylist,
  removeFromPlaylist,
  reorderPlaylist,
  type PlaylistDetail,
  type PlaylistSummary,
} from "../api/social";
import { useAuth } from "../auth/AuthContext";

export function PlaylistsPage() {
  const { ready, user } = useAuth();
  const [items, setItems] = useState<PlaylistSummary[] | null>(null);
  const [title, setTitle] = useState("");
  const [visibility, setVisibility] = useState("private");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    if (!ready || !user) {
      return;
    }
    channelPlaylists(user.channel.handle)
      .then((page) => setItems(page.items))
      .catch((caught: unknown) => {
        setError(caught instanceof ApiError ? caught.message : "Could not load playlists.");
      });
  }, [ready, user]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const created = await createPlaylist(title, "", visibility);
      setMessage("Playlist created.");
      navigate(`/playlists/${created.id}`);
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Could not create the playlist.");
    }
  }

  if (!ready) {
    return <p>Loading playlists.</p>;
  }
  if (!user) {
    return <p>Log in to manage playlists.</p>;
  }
  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">Playlists</h1>
      {error ? <p role="alert" className="text-sm text-red-300">{error}</p> : null}
      {message ? <p className="text-sm text-emerald-300">{message}</p> : null}
      <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => void onCreate(event)}>
        <label className="text-sm">
          Title
          <input value={title} onChange={(event) => setTitle(event.target.value)} required className="mt-1 block rounded-lg border border-line bg-panel px-3 py-2" />
        </label>
        <label className="text-sm">
          Visibility
          <select aria-label="Playlist visibility" value={visibility} onChange={(event) => setVisibility(event.target.value)} className="mt-1 block rounded-lg border border-line bg-panel px-3 py-2">
            <option value="private">private</option>
            <option value="unlisted">unlisted</option>
            <option value="public">public</option>
          </select>
        </label>
        <button type="submit" className="rounded-full bg-accent px-4 py-2 text-sm">Create playlist</button>
      </form>
      {items === null && !error ? <p>Loading playlists.</p> : null}
      {items && items.length === 0 ? <p className="text-sm text-muted">No playlists yet.</p> : null}
      <ul className="space-y-2">
        {items?.map((playlist) => (
          <li key={playlist.id}>
            <Link to={`/playlists/${playlist.id}`} className="hover:underline">{playlist.title}</Link>
            <span className="ml-2 text-xs text-muted">{playlist.visibility}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function PlaylistDetailPage() {
  const { playlistId = "" } = useParams();
  const { ready } = useAuth();
  const [playlist, setPlaylist] = useState<PlaylistDetail | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!ready || !playlistId) {
      return;
    }
    getPlaylist(playlistId)
      .then(setPlaylist)
      .catch((caught: unknown) => {
        setError(caught instanceof ApiError ? caught.message : "Could not load this playlist.");
      });
  }, [ready, playlistId]);

  if (!ready || (!playlist && !error)) {
    return <p>Loading playlist.</p>;
  }
  if (error) {
    return <p role="alert" className="text-sm text-red-300">{error}</p>;
  }
  if (!playlist) {
    return <p>Loading playlist.</p>;
  }
  const ids = playlist.items.map((item) => item.video_id);
  return (
    <article className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold">{playlist.title}</h1>
      <p className="mt-2 text-sm text-muted">{playlist.visibility} · {playlist.channel.name}</p>
      <p className="mt-4 whitespace-pre-wrap text-sm">{playlist.description || "No description yet."}</p>
      {message ? <p className="mt-3 text-sm text-emerald-300">{message}</p> : null}
      {playlist.items.length === 0 ? <p className="mt-6 text-sm text-muted">This playlist is empty.</p> : null}
      <ol className="mt-4 space-y-3">
        {playlist.items.map((item, index) => (
          <li key={item.video_id} className="flex items-center justify-between gap-3 rounded-lg border border-line p-3 text-sm">
            {item.unavailable || !item.title ? (
              <span>Unavailable video</span>
            ) : (
              <Link to={`/videos/${item.video_id}`} className="hover:underline">{item.title}</Link>
            )}
            <span className="flex gap-2">
              <button type="button" className="underline" disabled={index === 0} onClick={() => void move(index, -1)}>Up</button>
              <button type="button" className="underline" disabled={index === ids.length - 1} onClick={() => void move(index, 1)}>Down</button>
              <button type="button" className="underline" onClick={() => void remove(item.video_id)}>Remove</button>
            </span>
          </li>
        ))}
      </ol>
      <button
        type="button"
        className="mt-6 text-sm text-red-300 underline"
        onClick={() => {
          void deletePlaylist(playlist.id).then(() => setMessage("Playlist deleted."));
        }}
      >
        Delete playlist
      </button>
    </article>
  );

  async function move(index: number, delta: number) {
    const next = [...ids];
    const swap = index + delta;
    const current = next[index];
    next[index] = next[swap];
    next[swap] = current;
    await reorderPlaylist(playlistId, next);
    setPlaylist(await getPlaylist(playlistId));
  }

  async function remove(videoId: string) {
    await removeFromPlaylist(playlistId, videoId);
    setPlaylist(await getPlaylist(playlistId));
  }
}
