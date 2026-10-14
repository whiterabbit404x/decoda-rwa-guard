import { SECTION_IDS } from './home-data';
import { ProductShowcase } from './product-showcase';
import { Reveal } from './scroll-reveal';
import { SectionHeading } from './site-ui';
import site from './site.module.css';

/**
 * Section D — the product experience: the nine screens a customer actually
 * uses, in the same order as the authenticated navigation.
 */
export function ProductSection() {
  return (
    <section id={SECTION_IDS.product} className={site.section} aria-labelledby="product-title">
      <div className={site.container}>
        <SectionHeading
          id="product-title"
          eyebrow="Product experience"
          title="The Workspace Your Security Team Operates In."
          lead="Nine connected screens take an analyst from posture to proof. The previews are illustrations with sample data; in the product, every screen shows only what your workspace actually reports — and says so when data is missing."
        />
        <Reveal variant="scale">
          <ProductShowcase />
        </Reveal>
      </div>
    </section>
  );
}
