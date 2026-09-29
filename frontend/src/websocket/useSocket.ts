import { useEffect, useRef, useState } from "react";
import { nodePath } from "../api/client";

// JSON WebSocket to bng-api with reconnect (backoff up to 10 s). `send` is kept for
// the session subscription; messages sent while disconnected are replayed on connect.
export function useSocket<T>(path: string, onMessage: (msg: T) => void, initial?: unknown) {
  const [connected, setConnected] = useState(false);
  const ws = useRef<WebSocket | null>(null);
  const last = useRef<unknown>(initial);
  const handler = useRef(onMessage);
  handler.current = onMessage;

  useEffect(() => {
    let closed = false;
    let delay = 1000;
    let timer: number | undefined;
    const connect = () => {
      const s = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${nodePath(path)}`);
      ws.current = s;
      s.onopen = () => {
        setConnected(true);
        delay = 1000;
        if (last.current !== undefined) s.send(JSON.stringify(last.current));
      };
      s.onmessage = (e) => handler.current(JSON.parse(e.data));
      s.onclose = () => {
        setConnected(false);
        if (!closed) timer = window.setTimeout(connect, (delay = Math.min(delay * 2, 10000)));
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      ws.current?.close();
    };
  }, [path]);

  const send = (msg: unknown) => {
    last.current = msg;
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(msg));
  };
  return { connected, send };
}
