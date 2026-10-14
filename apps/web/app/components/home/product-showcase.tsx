'use client';

import { useRef, useState, type KeyboardEvent } from 'react';

import { ILLUSTRATION_NOTE, PRODUCT_SCREENS } from './home-data';
import { HomeIcon } from './home-icons';
import { ProductPreview } from './product-previews';
import styles from './showcase.module.css';

/**
 * The product tour: the nine Guard screens as an accessible tab set.
 *
 * WAI-ARIA tabs with automatic activation — the arrow keys (either axis, since
 * the list is vertical on desktop and a horizontal strip on phones), Home and
 * End move focus AND select. The panel is keyed on the active screen, so each
 * switch replays its short CSS entrance; under reduced motion it simply swaps.
 * The visitor drives every change: nothing auto-advances.
 *
 * The illustrative window is aria-hidden — its sample values are not facts a
 * screen reader should announce — and the tab panel says in words what the
 * screen does.
 */
export function ProductShowcase() {
  const [active, setActive] = useState(0);
  const tabs = useRef<Array<HTMLButtonElement | null>>([]);
  const screen = PRODUCT_SCREENS[active];

  function select(index: number) {
    const next = (index + PRODUCT_SCREENS.length) % PRODUCT_SCREENS.length;
    setActive(next);
    tabs.current[next]?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    switch (event.key) {
      case 'ArrowDown':
      case 'ArrowRight':
        event.preventDefault();
        select(active + 1);
        break;
      case 'ArrowUp':
      case 'ArrowLeft':
        event.preventDefault();
        select(active - 1);
        break;
      case 'Home':
        event.preventDefault();
        select(0);
        break;
      case 'End':
        event.preventDefault();
        select(PRODUCT_SCREENS.length - 1);
        break;
      default:
        break;
    }
  }

  return (
    <div className={styles.showcase}>
      <div className={styles.tablist} role="tablist" aria-label="Guard product screens" aria-orientation="vertical">
        {PRODUCT_SCREENS.map((item, index) => (
          <button
            key={item.key}
            ref={(node) => {
              tabs.current[index] = node;
            }}
            type="button"
            role="tab"
            id={`product-tab-${item.key}`}
            aria-selected={index === active}
            aria-controls="product-panel"
            tabIndex={index === active ? 0 : -1}
            className={styles.tab}
            onClick={() => setActive(index)}
            onKeyDown={onKeyDown}
          >
            <span className={styles.tabIcon} aria-hidden="true">
              <HomeIcon name={item.icon} size={17} />
            </span>
            <span className={styles.tabLabel}>{item.label}</span>
          </button>
        ))}
      </div>

      <div
        key={screen.key}
        id="product-panel"
        role="tabpanel"
        aria-labelledby={`product-tab-${screen.key}`}
        className={styles.panel}
        tabIndex={0}
      >
        <div className={styles.panelCopy}>
          <p className={styles.panelKicker}>{screen.label}</p>
          <h3 className={styles.panelTitle}>{screen.title}</h3>
          <p className={styles.panelBody}>{screen.body}</p>
          <ul className={styles.panelPoints}>
            {screen.points.map((point) => (
              <li key={point}>
                <HomeIcon name="check" size={14} />
                {point}
              </li>
            ))}
          </ul>
        </div>
        <div className={styles.panelVisual} aria-hidden="true">
          <ProductPreview screen={screen.key} />
        </div>
        <p className={styles.panelNote}>{ILLUSTRATION_NOTE}</p>
      </div>
    </div>
  );
}
