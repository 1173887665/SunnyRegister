import { useEffect } from "react";
import type { RefObject } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";

gsap.registerPlugin(useGSAP);

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function shouldReduceMotion() {
  return typeof window !== "undefined" && window.matchMedia(REDUCED_MOTION).matches;
}

function markAnimated(el: Element, key: string) {
  const node = el as HTMLElement;
  const prop = `sunnyGsap${key}`;
  if (node.dataset[prop] === "1") return false;
  node.dataset[prop] = "1";
  return true;
}

function animateModal(mask: Element) {
  if (!markAnimated(mask, "Modal")) return;
  const panel = mask.querySelector<HTMLElement>(".sr-modal");
  const tl = gsap.timeline({ defaults: { ease: "power3.out" } });
  tl.fromTo(mask, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.18 });
  if (panel) {
    tl.fromTo(
      panel,
      { autoAlpha: 0, y: 24, scale: 0.972 },
      {
        autoAlpha: 1,
        y: 0,
        scale: 1,
        duration: 0.36,
        ease: "back.out(1.08)",
        clearProps: "transform,opacity,visibility",
      },
      "<0.03",
    );
  }
}

function animateToast(toast: Element) {
  if (!markAnimated(toast, "Toast")) return;
  gsap.fromTo(
    toast,
    { autoAlpha: 0, y: -12, scale: 0.98 },
    {
      autoAlpha: 1,
      y: 0,
      scale: 1,
      duration: 0.26,
      ease: "power3.out",
      clearProps: "transform,opacity,visibility",
    },
  );
}

export function useSunnyGsap(rootRef: RefObject<HTMLElement | null>, pageKey: string) {
  useGSAP(
    () => {
      const root = rootRef.current;
      if (!root) return;

      const mm = gsap.matchMedia();
      mm.add({ reduceMotion: REDUCED_MOTION }, (context) => {
        if (context.conditions?.reduceMotion) return;

        const revealTargets = gsap.utils.toArray<HTMLElement>(
          ".hero-card, .sr-toolbar, .sr-table-card, .sr-log-card, .sr-proxy-stat, .soft-table",
          root,
        ).filter((target) => target.offsetParent !== null).slice(0, 8);
        if (revealTargets.length) {
          gsap.fromTo(
            revealTargets,
            { autoAlpha: 0, y: 10 },
            {
              autoAlpha: 1,
              y: 0,
              duration: 0.24,
              ease: "power3.out",
              stagger: { each: 0.025, from: "start" },
              clearProps: "transform,opacity,visibility",
            },
          );
        }

        const visibleHero = gsap.utils.toArray<HTMLElement>(".hero-card", root).find((target) => target.offsetParent !== null);
        const heroTitle = visibleHero?.querySelector<HTMLElement>("h1");
        const heroDesc = visibleHero?.querySelector<HTMLElement>("p");
        if (heroTitle) {
          gsap.fromTo(
            [heroTitle, heroDesc].filter(Boolean),
            { autoAlpha: 0, y: 14 },
            {
              autoAlpha: 1,
              y: 0,
              duration: 0.42,
              ease: "power3.out",
              stagger: 0.06,
              clearProps: "transform,opacity,visibility",
            },
          );
        }
      });

      return () => mm.revert();
    },
    { scope: rootRef, dependencies: [pageKey], revertOnUpdate: true },
  );

  useEffect(() => {
    if (shouldReduceMotion()) return;

    document.querySelectorAll(".sr-modal-mask").forEach(animateModal);
    document.querySelectorAll(".sr-toast").forEach(animateToast);

    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        mutation.addedNodes.forEach((node) => {
          if (!(node instanceof Element)) return;
          if (node.matches(".sr-modal-mask")) animateModal(node);
          if (node.matches(".sr-toast")) animateToast(node);
          node.querySelectorAll?.(".sr-modal-mask").forEach(animateModal);
          node.querySelectorAll?.(".sr-toast").forEach(animateToast);
        });
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);
}

export function useTopBarGsap(rootRef: RefObject<HTMLElement | null>, activeKey: string) {
  useGSAP(
    () => {
      const root = rootRef.current;
      if (!root || shouldReduceMotion()) return;
      const activeNav = root.querySelector<HTMLElement>("[data-sunny-nav-active='true']");
      if (!activeNav) return;

      gsap.fromTo(
        activeNav,
        { scale: 0.96 },
        {
          scale: 1,
          duration: 0.28,
          ease: "back.out(1.7)",
          clearProps: "transform",
        },
      );
    },
    { scope: rootRef, dependencies: [activeKey], revertOnUpdate: true },
  );
}
