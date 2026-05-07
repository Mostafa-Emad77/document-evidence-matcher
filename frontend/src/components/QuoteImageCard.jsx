import styles from './QuoteImageCard.module.css'

export default function QuoteImageCard({ link, image }) {
  const imageSrc = image?.thumbnail_base64
    ? `data:image/png;base64,${image.thumbnail_base64}`
    : null

  return (
    <div className={styles.card}>
      <div className={styles.row}>
        <div className={styles.col}>
          <span className={styles.label}>Description</span>
          <p>{link.description_text}</p>
        </div>
        <div className={styles.col}>
          <span className={styles.label}>Quote</span>
          <p className={styles.quote}>{link.quote_text}</p>
        </div>
      </div>
      {link.citation_text && (
        <p className={styles.citation}>📎 {link.citation_text}</p>
      )}
      {imageSrc && (
        <div className={styles.imageWrap}>
          <p className={styles.imageHint}>Matched evidence page (from uploaded PDF)</p>
          <img className={styles.image} src={imageSrc} alt={image?.src || 'Quote screenshot'} />
        </div>
      )}
    </div>
  )
}
