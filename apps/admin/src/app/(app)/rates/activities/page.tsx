import { CatalogueResource } from "@/components/app/catalogue-resource";
import { ACTIVITY_RATES } from "@/lib/catalogue";

export default function Page() {
  return <CatalogueResource spec={ACTIVITY_RATES} />;
}
