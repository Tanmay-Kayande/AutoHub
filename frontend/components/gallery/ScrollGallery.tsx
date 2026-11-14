"use client";

// Pinned, scroll-driven cross-fade gallery (docs/V2/FRONTEND_DESIGN.md §4).
// Desktop: an N×100vh section with a sticky stage; each slide's opacity, scale and
// parallax are functions of scroll progress (Motion values, no React state per frame).
// Mobile (<768px) or reduced motion: a horizontal scroll-snap carousel.
import { motion, useMotionValueEvent, useReducedMotion, useScroll, useTransform, type MotionValue } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { SmartImage } from "@/components/ui/SmartImage";
import type { VehicleImage } from "@/lib/types";

/** Scroll offsets must lie in [0, 1] and never decrease (Web Animations keyframe offsets). */
function offsets(points: number[]): number[] {
  const out: number[] = [];
  for (const p of points) out.push(Math.max(out.at(-1) ?? 0, Math.min(1, Math.max(0, p))));
  return out;
}

function useIsDesktop(): boolean {
  const [desktop, setDesktop] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(min-width: 768px)");
    const update = () => setDesktop(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return desktop;
}

function Caption({ image, index, total }: { image: VehicleImage; index: number; total: number }) {
  return (
    <span className="text-xs text-muted">
      <span className="tabular text-text">
        {String(index + 1).padStart(2, "0")} / {String(total).padStart(2, "0")}
      </span>
      <span className="mx-2">·</span>
      {image.is_official ? <span className="text-accent">Official · </span> : null}
      {image.source_page_url ? (
        <a href={image.source_page_url} target="_blank" rel="noreferrer" className="hover:text-text">
          {image.source_domain ?? "source"}
        </a>
      ) : (
        (image.source_domain ?? "source unknown")
      )}
    </span>
  );
}

function Slide({
  image,
  index,
  total,
  progress,
  near,
  alt,
  onFail,
}: {
  image: VehicleImage;
  index: number;
  total: number;
  progress: MotionValue<number>;
  near: boolean;
  alt: string;
  onFail: () => void;
}) {
  const step = 1 / total;
  const start = index * step;
  const end = start + step;
  const fade = step * 0.35;
  const opacity = useTransform(
    progress,
    offsets([start - fade, start + fade * 0.2, end - fade * 0.2, end + fade]),
    index === 0 ? [1, 1, 1, 0] : index === total - 1 ? [0, 1, 1, 1] : [0, 1, 1, 0],
  );
  const scale = useTransform(progress, offsets([start - fade, end]), [1.08, 1]);
  const y = useTransform(progress, offsets([start, end]), ["2%", "-2%"]);
  return (
    <motion.div style={{ opacity, scale, y }} className="absolute inset-0 will-change-transform" aria-hidden={!near}>
      {near ? (
        <SmartImage src={image.image_url} alt={alt} className="h-full w-full" onFail={onFail} eager={index === 0} />
      ) : (
        <div className="h-full w-full bg-surface" />
      )}
    </motion.div>
  );
}

function PinnedGallery({ images, title, alt }: { images: VehicleImage[]; title: string; alt: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState<Set<number>>(new Set());
  const shown = images.filter((image) => !failed.has(image.id));
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start start", "end end"] });
  const [current, setCurrent] = useState(0);
  useMotionValueEvent(scrollYProgress, "change", (value) => {
    const next = Math.min(shown.length - 1, Math.max(0, Math.floor(value * shown.length)));
    if (next !== current) setCurrent(next);
  });
  const rail = useTransform(scrollYProgress, [0, 1], ["0%", "100%"]);
  if (!shown.length) return null;

  return (
    <section ref={ref} style={{ height: `${shown.length * 100}vh` }} className="relative" aria-label={`${title} gallery`}>
      <div className="sticky top-16 h-[calc(100vh-4rem)] overflow-hidden">
        {shown.map((image, index) => (
          <Slide
            key={image.id}
            image={image}
            index={index}
            total={shown.length}
            progress={scrollYProgress}
            near={Math.abs(index - current) <= 1}
            alt={`${alt} — ${title.toLowerCase()} ${index + 1}`}
            onFail={() => setFailed((prev) => new Set(prev).add(image.id))}
          />
        ))}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-bg/90 via-transparent to-bg/30" />
        <div className="absolute inset-x-0 bottom-0 mx-auto flex max-w-7xl items-end justify-between gap-6 px-6 pb-8">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-accent">{title}</p>
            <Caption image={shown[current] ?? shown[0]} index={current} total={shown.length} />
          </div>
          <div className="h-px w-40 bg-line" aria-hidden>
            <motion.div style={{ width: rail }} className="h-px bg-accent" />
          </div>
        </div>
      </div>
    </section>
  );
}

function Carousel({ images, title, alt }: { images: VehicleImage[]; title: string; alt: string }) {
  const [failed, setFailed] = useState<Set<number>>(new Set());
  const shown = images.filter((image) => !failed.has(image.id));
  if (!shown.length) return null;
  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6" aria-label={`${title} gallery`}>
      <p className="mb-3 text-xs uppercase tracking-[0.2em] text-accent">{title}</p>
      <div className="no-scrollbar flex snap-x snap-mandatory gap-3 overflow-x-auto pb-2">
        {shown.map((image, index) => (
          <figure key={image.id} className="w-[85%] shrink-0 snap-center sm:w-[60%]">
            <SmartImage
              src={image.image_url}
              alt={`${alt} — ${title.toLowerCase()} ${index + 1}`}
              className="aspect-[16/10] w-full rounded-2xl"
              onFail={() => setFailed((prev) => new Set(prev).add(image.id))}
            />
            <figcaption className="mt-2">
              <Caption image={image} index={index} total={shown.length} />
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}

export function ScrollGallery({ images, title, alt }: { images: VehicleImage[]; title: string; alt: string }) {
  const desktop = useIsDesktop();
  const reduced = useReducedMotion();
  const usable = images.filter((i) => i.validation_status !== "broken" && i.validation_status !== "rejected");
  if (usable.length === 0) return null;
  if (usable.length === 1) {
    return (
      <section className="mx-auto max-w-7xl px-4 sm:px-6" aria-label={`${title} image`}>
        <p className="mb-3 text-xs uppercase tracking-[0.2em] text-accent">{title}</p>
        <SmartImage src={usable[0].image_url} alt={`${alt} — ${title.toLowerCase()}`} className="aspect-[16/9] w-full rounded-3xl" />
        <div className="mt-2">
          <Caption image={usable[0]} index={0} total={1} />
        </div>
      </section>
    );
  }
  return desktop && !reduced ? (
    <PinnedGallery images={usable} title={title} alt={alt} />
  ) : (
    <Carousel images={usable} title={title} alt={alt} />
  );
}
