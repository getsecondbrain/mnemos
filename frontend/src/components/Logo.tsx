import { useState, useEffect } from "react";
import { Link } from "react-router-dom";

function randomSmudge(): React.CSSProperties | null {
  // ~12% chance of a clean logo — makes the smudge reappearance more maddening
  if (Math.random() < 0.12) return null;
  const r = () => Math.random();
  return {
    position: "absolute",
    top: `${-2 + r() * 24}px`,
    left: `${r() * 75}px`,
    width: `${3 + r() * 6}px`,
    height: `${2 + r() * 5}px`,
    opacity: 0.06 + r() * 0.1,
    borderRadius: `${30 + r() * 70}%`,
    transform: `rotate(${r() * 180}deg)`,
    filter: `blur(${1.5 + r() * 2}px)`,
    background: `rgba(${150 + r() * 40 | 0},${130 + r() * 30 | 0},${110 + r() * 20 | 0},1)`,
    pointerEvents: "none",
  };
}

export default function Logo() {
  const [smudge, setSmudge] = useState<React.CSSProperties | null>(randomSmudge);

  useEffect(() => {
    let id: ReturnType<typeof setTimeout>;
    (function cycle() {
      // 30s–5min, biased toward shorter waits
      const delay = 30_000 + (Math.random() ** 1.6) * 270_000;
      id = setTimeout(() => { setSmudge(randomSmudge()); cycle(); }, delay);
    })();
    return () => clearTimeout(id);
  }, []);

  return (
    <Link
      to="/timeline"
      className="relative text-2xl font-bold tracking-tight hover:text-gray-300 transition-colors inline-block select-none"
    >
      Mnemos
      {smudge && <span style={smudge} />}
    </Link>
  );
}
