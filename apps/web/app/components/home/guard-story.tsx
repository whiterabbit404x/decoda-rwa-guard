'use client';

import { useEffect, useRef, useState } from 'react';

import { ILLUSTRATION_NOTE, STORY_STEPS } from './home-data';
import { HomeIcon } from './home-icons';
import { StoryVisual } from './story-visuals';
import styles from './story.module.css';

function stepState(index: number, active: number): 'done' | 'active' | 'todo' {
  if (index < active) return 'done';
  return index === active ? 'active' : 'todo';
}

/**
 * Guard's incident lifecycle, told as five steps. On wide screens the step
 * crossing the middle of the viewport drives a sticky illustration beside the
 * text, and a rail fills as the reader moves through the sequence; on narrow
 * screens each step carries its own illustration.
 *
 * The active step is exposed as attributes only — `data-state` on each step
 * and `data-progress` on the story — which story.module.css reads. Inactive
 * steps change colour, never opacity, so every paragraph stays at full
 * contrast. The first step is active from the server render on, so the
 * sequence reads correctly with no JavaScript at all.
 */
export function GuardStory() {
  const [active, setActive] = useState(0);
  const refs = useRef<Array<HTMLLIElement | null>>([]);

  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return undefined;
    // A thin band across the middle of the viewport: whichever step crosses it is active.
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const index = refs.current.indexOf(entry.target as HTMLLIElement);
          if (index >= 0) setActive(index);
        }
      },
      { rootMargin: '-46% 0px -46% 0px' },
    );
    for (const node of refs.current) if (node) observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div className={styles.story} data-progress={active}>
      <div className={styles.stepsWrap}>
        <span className={styles.rail} aria-hidden="true">
          <span className={styles.railFill} />
        </span>
        <ol className={styles.steps}>
          {STORY_STEPS.map((step, index) => (
            <li
              key={step.key}
              ref={(node) => {
                refs.current[index] = node;
              }}
              className={styles.step}
              data-state={stepState(index, active)}
            >
              <span className={styles.node} aria-hidden="true">
                <HomeIcon name={step.icon} size={18} />
              </span>
              <p className={styles.kicker}>
                <span>{String(index + 1).padStart(2, '0')}</span>
                {step.label}
              </p>
              <h3 className={styles.stepTitle}>{step.title}</h3>
              <p className={styles.stepBody}>{step.body}</p>
              <ul className={styles.points}>
                {step.points.map((point) => (
                  <li key={point}>
                    <HomeIcon name="check" size={15} />
                    {point}
                  </li>
                ))}
              </ul>
              <div className={styles.inlineVisual} aria-hidden="true">
                <StoryVisual step={step.key} />
              </div>
            </li>
          ))}
        </ol>
      </div>

      <div className={styles.stage} aria-hidden="true">
        <div className={styles.stageFrame}>
          <div className={styles.stageTabs}>
            {STORY_STEPS.map((step, index) => (
              <span key={step.key} data-state={stepState(index, active)}>
                {step.label}
              </span>
            ))}
          </div>
          <div className={styles.stagePanels}>
            {STORY_STEPS.map((step, index) => (
              <div key={step.key} className={styles.stagePanel} data-active={index === active ? 'true' : undefined}>
                <StoryVisual step={step.key} />
              </div>
            ))}
          </div>
        </div>
        <p className={styles.stageTag}>{ILLUSTRATION_NOTE}</p>
      </div>
    </div>
  );
}
