import { Typography } from "antd"

const { Title, Paragraph } = Typography

export default function HomePage() {
  return (
    <main
      style={{
        maxWidth: 960,
        margin: "0 auto",
        padding: "4rem 1.5rem",
      }}
    >
      <Title level={1}>核燃料与材料物性数据库</Title>
      <Paragraph>
        可持续共享的核燃料与材料物性数据库平台
      </Paragraph>
      <Paragraph type="secondary">
        Nuclear Fuel &amp; Materials Properties Database — a sustainable and
        sharing platform for nuclear materials data in China.
      </Paragraph>
    </main>
  )
}
