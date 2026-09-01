import { useEffect, useState } from "react";
import FreeppWorkbench from "@/freepp-workbench/FreeppWorkbench";
import "@/freepp-workbench/scoped.css";
import "./LinkWorkbenchHost.css";

export default function LinkWorkbench() {
  const [theme, setTheme] = useState<"light" | "dark">(() => document.documentElement.classList.contains("light") ? "light" : "dark");

  useEffect(() => {
    const root = document.documentElement;
    const sync = () => setTheme(root.classList.contains("light") ? "light" : "dark");
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return <section className="freepp-workbench-host" data-theme={theme}><FreeppWorkbench /></section>;
}
